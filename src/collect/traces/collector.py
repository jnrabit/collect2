"""Trace-Collector — schreibt Modellaufrufe als Trainings-Traces (JSONL).

Prinzip: nur mitschreiben, nie eingreifen (Anti-Scaffold — der Consumer, der
Finetune, existiert). Fehler im Collector dürfen NIE den Agenten-Pfad
brechen: jeder Write ist try/except-gekapselt, Fehler werden nur geloggt.

Append-only, rotierend pro Tag: data/traces/YYYY-MM-DD.jsonl. outcome wird
NICHT in-place aktualisiert (append-only), sondern als separate Outcome-Zeile
in data/traces/outcomes.jsonl geschrieben; der Validator/Curator joint über
workflow_id.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date
from pathlib import Path
from typing import Optional

from collect.config import settings
from collect.traces.schema import (
    TraceEntry, TraceMeta, compute_schema_hash, make_trace_id,
)

logger = logging.getLogger("traces.collector")
COLLECT2_VERSION = "637a91a"  # git-hash beim Anlegen; via _git_hash() aktualisierbar


def _git_hash() -> str:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent, text=True,
            stderr=subprocess.DEVNULL).strip()
    except Exception:
        return COLLECT2_VERSION


class TraceCollector:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir or settings.traces_dir)
        self._lock = threading.Lock()
        self._schema_hash = compute_schema_hash()
        self._version = _git_hash()
        self._count = 0

    def _today_file(self) -> Path:
        return self.base_dir / f"{date.today().isoformat()}.jsonl"

    def record(self, step_kind: str, messages: list[dict],
               tools: Optional[list[dict]] = None,
               workflow_id: str = "", step_index: int = 0,
               extra: Optional[dict] = None,
               provenance: Optional[dict] = None) -> Optional[str]:
        """Schreibt einen Trace. Gibt trace_id zurück (oder None bei Fehler).

        Wirft NIE — der Agenten-Pfad ist heilig. tools=None → leeres tools-Feld
        (der heutige deterministische Aufruf nutzte keine Tools).

        provenance: {model, model_digest, quant, driver} — wer den Trace
        erzeugt hat. Fehlende Felder werden zu None, NIE zur Exception: ein
        unvollstaendig beschrifteter Trace ist besser als ein gebrochener
        Agenten-Aufruf. Nichts wird geraten — was nicht uebergeben wurde,
        bleibt None.
        """
        try:
            trace_id = make_trace_id(messages)
            prov = provenance or {}
            entry = TraceEntry(
                messages=messages,
                tools=tools or [],
                meta=TraceMeta(
                    trace_id=trace_id, step_kind=step_kind,
                    step_index=step_index, workflow_id=workflow_id,
                    timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    collect2_version=self._version,
                    tool_schema_hash=self._schema_hash,
                    model=prov.get("model"),
                    model_digest=prov.get("model_digest"),
                    quant=prov.get("quant"),
                    driver=prov.get("driver"),
                    extra=extra or {}),
            )
            with self._lock:
                self.base_dir.mkdir(parents=True, exist_ok=True)
                with open(self._today_file(), "a", encoding="utf-8") as f:
                    f.write(entry.to_json() + "\n")
                self._count += 1
            return trace_id
        except Exception as e:  # noqa: BLE001 — Collector darf nie den Aufruf brechen
            logger.warning("Trace-Write fehlgeschlagen (ignoriert): %s", e)
            return None

    def record_outcome(self, workflow_id: str, outcome: str) -> None:
        """Append-only Outcome-Zeile (kein In-Place-Update der Trace-JSONL)."""
        if not workflow_id:
            return
        try:
            with self._lock:
                self.base_dir.mkdir(parents=True, exist_ok=True)
                with open(self.base_dir / "outcomes.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "workflow_id": workflow_id, "outcome": outcome,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.warning("Outcome-Write fehlgeschlagen (ignoriert): %s", e)

    def load_all(self, dedup: bool = True) -> list[dict]:
        """Alle Traces aus allen Tagesdateien (chronologisch).

        dedup=True (Default): exakte Duplikate über die trace_id zusammenfassen
        — die deterministische Pipeline ruft z. B. den Rewriter mehrfach pro
        Query mit identischem Prompt auf, was denselben Trace mehrfach schreibt.
        Fürs Training zählt nur das Unique."""
        out: list[dict] = []
        seen: set[str] = set()
        if not self.base_dir.exists():
            return out
        for f in sorted(self.base_dir.glob("*.jsonl")):
            # Seitenkanaele sind keine Traces: outcomes (append-only Ergebnis)
            # und flags (append-only Fehler-Inventur) liegen im selben Ordner.
            if f.name in ("outcomes.jsonl", "flags.jsonl", "validation_report.json"):
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = d.get("meta", {}).get("trace_id")
                if dedup and tid:
                    if tid in seen:
                        continue
                    seen.add(tid)
                out.append(d)
        return out

    def stats(self) -> dict:
        traces = self.load_all()
        synth = sum(1 for t in traces if t.get("meta", {}).get("synthetic"))
        kinds: dict[str, int] = {}
        for t in traces:
            k = t.get("meta", {}).get("step_kind", "?")
            kinds[k] = kinds.get(k, 0) + 1
        return {"total": len(traces), "synthetic": synth,
                "positive": len(traces) - synth, "step_kinds": kinds}


_collector: Optional[TraceCollector] = None
_collector_lock = threading.Lock()


def get_collector() -> TraceCollector:
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = TraceCollector()
    return _collector


def record_if_enabled(step_kind: str, messages: list[dict],
                      tools: Optional[list[dict]] = None, **kw) -> None:
    """Einhänge-Helfer für die Agenten: no-op wenn Traces aus, nie werfend."""
    if not settings.traces_enabled:
        return
    try:
        get_collector().record(step_kind, messages, tools, **kw)
    except Exception:  # noqa: BLE001 — doppelte Absicherung am Einhängepunkt
        pass
