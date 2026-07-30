"""Fehler-Inventur — Flags schreiben/lesen und aggregieren.

Zweck (Auftrag Fehler-Inventur): nachweisen, ob es überhaupt etwas zu lernen
gibt, BEVOR ein Finetune gebaut wird. Die Markierung ist bewusst menschlich —
ein Klassifikator würde genau die blinden Flecken erben, um die es geht.

Speicherung wie bei den Outcome-Zeilen: **append-only** nach
`data/traces/flags.jsonl`, nie In-Place-Update der Trace-Dateien. Ein Trace
darf mehrere Fehler haben; `--class none` hebt alle vorherigen auf
(Korrekturpfad ohne Löschen).

Reine Logik, kein Transport: die CLI (cli.py) ruft hier hinein, dieses Modul
kennt weder argparse noch Terminal.

Rubrik + Kalibrierbeispiele: `docs/traces_fehlerklassen.md` (maßgeblich).
Schwelle: `docs/finetune_gate.md`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable, Optional

from collect.config import settings

FLAGS_FILE = "flags.jsonl"
NONE = "none"

#: Klasse → Einzeiler (dieselbe Liste wie die Rubrik; hier maschinenlesbar,
#: damit `flag --help` sie ohne Nachschlagen zeigt).
FEHLERKLASSEN: dict[str, str] = {
    "tool_format": "Tool-Call syntaktisch/schematisch falsch "
                   "(erfundenes Tool, fehlender Pflichtparameter, kaputtes JSON)",
    "tool_unnoetig": "Call, wo keiner nötig war (T5-Muster)",
    "tool_fehlend": "kein Call, wo einer nötig war",
    "rewrite_falsch": "Rewrite verfehlt/verliert den Antezedenten oder erfindet Kontext",
    "rewrite_unnoetig": "eigenständige Frage umformuliert statt UNCHANGED",
    "rollenbruch": "Modell verlässt die zugewiesene Rolle "
                   "(antwortet inhaltlich statt zu rewriten o. ä.)",
    "register": "Sprache/Register verfehlt (Englisch/Chinesisch auf deutsche "
                "Anfrage, Sie-Form, Identity-Leak)",
    "format": "Output-Format verletzt (mehrzeilig wo einzeilig gefordert, Geschwätz)",
    "multi_step": "Fehler entsteht erst im Zusammenspiel mehrerer Schritte",
    "konfabulation": "inhaltlich erfundene Aussage mit Sicherheitston",
    "blindflug": "Agent legt Dateien an/ändert Code, ohne vorher die Zielstruktur "
                 "zu lesen (kein ls/Read) — Dateien am falschen Ort, "
                 "existierende Konventionen ignoriert, Code dupliziert",
    "kein_trace": "kein echter Modellausgang (Testfixture/Platzhalter im Bestand) "
                  "— Korpusdefekt, KEIN Modellfehler",
    "sonstiges": "passt in keine Klasse — Freitext (--note) Pflicht",
}

#: Klassen, die einen Defekt des BESTANDS oder des PROZESSES bezeichnen, nicht
#: des Modells. Sie zaehlen nicht in die Fehlerquote — sonst misst man die
#: eigene Testdaten-Verschmutzung oder Agent-Prozessfehler als Modellschwaeche
#: und entscheidet den Finetune auf einer falschen Zahl.
NICHT_MODELLFEHLER = frozenset({"kein_trace", "blindflug"})

#: Klasse → existiert eine Quelle, die die Zielfähigkeit selbst beherrscht?
#: Entscheidet im Gate mit, ob eine Klasse überhaupt antrainierbar ist
#: (Lehre aus Charge 4: aus einem hedgenden Lehrer lässt sich Entscheiden
#: nicht destillieren).
KLASSEN_QUELLE: dict[str, str] = {
    "tool_format": "C beherrscht es",
    "tool_unnoetig": "C beherrscht es",
    "tool_fehlend": "C beherrscht es",
    "rewrite_falsch": "C beherrscht es",
    "rewrite_unnoetig": "C beherrscht es",
    "rollenbruch": "C beherrscht es",
    "register": "C beherrscht es",
    "format": "C beherrscht es",
    "multi_step": "ungeklärt — bis zum Beleg: nur Handarbeit",
    "konfabulation": "nur Handarbeit",
    "blindflug": "prozessual — Workflow-Prompt-Regel adressiert es (Pre-Flight)",
    "kein_trace": "— (Korpusdefekt: ausschliessen, nicht lernen)",
    "sonstiges": "—",
}


class UnbekannteKlasse(ValueError):
    """Klasse steht nicht in der Rubrik."""


def flags_path(base_dir: Optional[Path] = None) -> Path:
    return Path(base_dir or settings.traces_dir) / FLAGS_FILE


def validate_klasse(klasse: str, note: str = "") -> None:
    """Wirft UnbekannteKlasse mit der Liste der gültigen Klassen."""
    if klasse == NONE:
        return
    if klasse not in FEHLERKLASSEN:
        gueltig = ", ".join([*FEHLERKLASSEN, NONE])
        raise UnbekannteKlasse(
            f"unbekannte Klasse {klasse!r}. Gültig: {gueltig}")
    if klasse == "sonstiges" and not note.strip():
        raise UnbekannteKlasse(
            "Klasse 'sonstiges' verlangt --note (sonst ist der Fall später "
            "nicht rekonstruierbar und taugt nicht als Beleg für eine neue Klasse)")


def write_flag(trace_id: str, klasse: str, note: str = "",
               flagger: str = "", base_dir: Optional[Path] = None) -> dict:
    """Append-only eine Flag-Zeile. Gibt den geschriebenen Datensatz zurück."""
    validate_klasse(klasse, note)
    if not trace_id:
        raise ValueError("trace_id fehlt")
    row = {
        "trace_id": trace_id,
        "klasse": klasse,
        "note": note,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "flagger": flagger or os.environ.get("USER", "unbekannt"),
    }
    path = flags_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def load_flags(base_dir: Optional[Path] = None) -> list[dict]:
    """Alle Flag-Zeilen in Schreibreihenfolge (kaputte Zeilen werden übersprungen)."""
    path = flags_path(base_dir)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def effective(flags: Iterable[dict]) -> dict[str, set[str]]:
    """trace_id → wirksame Fehlerklassen, nach Anwendung der `none`-Aufhebung.

    Chronologisch (Schreibreihenfolge): jedes `none` leert die Menge für diesen
    Trace. Ein Trace mit leerer Menge gilt als gesichtet-und-korrekt.
    """
    out: dict[str, set[str]] = {}
    for row in flags:
        tid = row.get("trace_id")
        klasse = row.get("klasse")
        if not tid or not klasse:
            continue
        bucket = out.setdefault(tid, set())
        if klasse == NONE:
            bucket.clear()
        else:
            bucket.add(klasse)
    return out


def gesichtet_ids(flags: Iterable[dict]) -> set[str]:
    """Alle trace_ids mit mindestens einer Flag-Zeile (auch `none`) — also
    alles, was ein Mensch angesehen hat."""
    return {r["trace_id"] for r in flags if r.get("trace_id")}


def _modell(trace: dict) -> str:
    """Provenienz-Schluessel: `modell @treiber (quant)`.

    Getrennte Ausweisung ist Pflicht, weil 3b-Rewrites und Schritte eines
    staerkeren Modells nicht dieselbe Verteilung sind — UND weil derselbe
    Modellname ueber einen anderen Treiber ein anderes Ergebnis liefert
    (gemessen: 17/24 transformers-fp16 gegen 13/24 ollama-q4, gleiches Modell).
    Altbestand ohne diese Felder bleibt 'unbekannt': nichts wird geraten.
    """
    meta = trace.get("meta", {})
    name = meta.get("model") or meta.get("modell") or meta.get("llm_model")
    if not name:
        return "unbekannt (vor Provenienz-Feldern)"
    teile = [str(name)]
    if meta.get("driver"):
        teile.append(f"@{meta['driver']}")
    if meta.get("quant"):
        teile.append(f"({meta['quant']})")
    return " ".join(teile)


def _leer_gruppe() -> dict:
    return {"gesamt": 0, "gesichtet": 0, "fehlerhaft": 0, "klassen": {}}


def aggregate(traces: list[dict], flags: Iterable[dict]) -> dict:
    """Verteilung der Fehlerklassen — die Zahl, die das Gate trägt.

    Prozente beziehen sich auf die GESICHTETEN Schritte, nicht auf den
    Gesamtbestand: was niemand angesehen hat, kann keine Fehlerquote haben.
    """
    flags = list(flags)
    eff = effective(flags)
    gesehen = gesichtet_ids(flags)

    total = len(traces)
    gesichtet = 0
    fehlerhaft = 0
    klassen: dict[str, int] = {}
    nach_kind: dict[str, dict] = {}
    nach_modell: dict[str, dict] = {}

    for t in traces:
        tid = t.get("meta", {}).get("trace_id", "")
        kind = t.get("meta", {}).get("step_kind", "?")
        modell = _modell(t)
        gk = nach_kind.setdefault(kind, _leer_gruppe())
        gm = nach_modell.setdefault(modell, _leer_gruppe())
        gk["gesamt"] += 1
        gm["gesamt"] += 1
        if tid not in gesehen:
            continue
        gesichtet += 1
        gk["gesichtet"] += 1
        gm["gesichtet"] += 1
        alle = eff.get(tid) or set()
        fehler = alle - NICHT_MODELLFEHLER
        if fehler:
            fehlerhaft += 1
            gk["fehlerhaft"] += 1
            gm["fehlerhaft"] += 1
        for k in alle:          # Korpusdefekte werden ausgewiesen, nur nicht
            klassen[k] = klassen.get(k, 0) + 1   # in die Quote gerechnet
            gk["klassen"][k] = gk["klassen"].get(k, 0) + 1
            gm["klassen"][k] = gm["klassen"].get(k, 0) + 1

    def anteil(n: int) -> float:
        return round(100.0 * n / gesichtet, 1) if gesichtet else 0.0

    klassen_liste = [
        {"klasse": k, "n": n, "anteil_prozent": anteil(n),
         "quelle": KLASSEN_QUELLE.get(k, "—")}
        for k, n in sorted(klassen.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {
        "total": total,
        "gesichtet": gesichtet,
        "ungesichtet": total - gesichtet,
        "fehlerhaft": fehlerhaft,
        "korrekt": gesichtet - fehlerhaft,
        "fehlerquote_prozent": anteil(fehlerhaft),
        "klassen": klassen_liste,
        "nach_step_kind": nach_kind,
        "nach_modell": nach_modell,
        "hinweis": ("Prozente beziehen sich auf gesichtete Schritte. "
                    "Modelle getrennt lesen — verschiedene Modelle sind nicht "
                    "dieselbe Verteilung."),
    }


def ungesichtete(traces: list[dict], flags: Iterable[dict]) -> list[dict]:
    """Traces ohne jede Flag-Zeile, in Bestandsreihenfolge (chronologisch)."""
    gesehen = gesichtet_ids(flags)
    return [t for t in traces
            if t.get("meta", {}).get("trace_id", "") not in gesehen]
