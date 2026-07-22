"""Autopilot — autonomer Self-Play-Loop für den Vault.

Erzeugt TF-IDF-gewichtete Queries aus Vault-Inhalten, führt sie durch
den existierenden RetrievalService und zeichnet Distillation-Daten auf.

Kein neuer LLM-Pfad — nutzt ausschließlich RetrievalService.retrieve().
Die gesammelten Distillation-Daten (Abstände, Zonen, Doc-Hits pro Query)
dienen später als Input für dream.py (Meta-Kristallisation).

Port aus ai_neu/autopilot.py; reduziert auf collect2-Kern (DESIGN.md §5).
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from collect.config import settings

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

DATA_DIR = Path.home() / "collect2" / "data"
SEEN_FILE = DATA_DIR / "autopilot_seen.json"
DISTILLATION_FILE = DATA_DIR / "distillation_data.jsonl"
DEFAULT_INTERVAL = 30  # Sekunden zwischen Queries
DEFAULT_MAX_CYCLES = 500

# Stopwörter für IDF — Wörter die in fast jedem Paper/Text auftauchen
_STOP: set[str] = {
    "using", "based", "model", "method", "results", "paper", "study",
    "analysis", "approach", "propose", "proposed", "present", "show",
    "shown", "result", "novel", "state", "large", "high", "data",
    "systems", "system", "network", "learning", "deep", "neural",
    "while", "which", "these", "their", "there", "about", "where",
    "between", "different", "through", "other", "also", "than",
    "from", "with", "that", "this", "have", "been", "more",
}
_IDF_CACHE: dict[str, float] = {}


# ── Seen-Tracking ─────────────────────────────────────────────────────────────


def _load_seen(path: Path) -> set[str]:
    if path.exists():
        try:
            return set(json.loads(path.read_text()))
        except Exception:
            pass
    return set()


def _save_seen(seen: set[str], path: Path) -> None:
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".autopilot_seen_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(sorted(seen), f)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning("seen-Datei nicht speicherbar: %s", e)


# ── IDF ───────────────────────────────────────────────────────────────────────


def _build_idf(docs: list[dict], max_sample: int = 5000) -> None:
    sample = random.sample(docs, min(max_sample, len(docs)))
    df: Counter = Counter()
    for doc in sample:
        text = (str(doc.get("title", "")) + " " + str(doc.get("content", "")))[:500]
        words = set(re.findall(r"\b[a-zA-Z]{5,}\b", text.lower()))
        df.update(words)
    n = len(sample)
    _IDF_CACHE.clear()
    _IDF_CACHE.update({w: np.log(n / (1 + cnt)) for w, cnt in df.items()})
    logger.info("IDF-Tabelle: %d Terme aus %d Docs", len(_IDF_CACHE), n)


# ── DE-Fallback ───────────────────────────────────────────────────────────────


def _extract_de_nouns(title: str, content: str) -> list[str]:
    text = title + " " + " ".join(str(content).split()[:60])
    nouns: Counter = Counter()
    for tok in re.findall(r"\b[A-ZÄÖÜ][a-zäöüß]{4,}\b", text):
        nouns[tok] += 1
    return [w.lower() for w, _ in nouns.most_common(6)]


# ── Query-Extraktion ──────────────────────────────────────────────────────────


def extract_query(doc: dict) -> str:
    title = str(doc.get("title", ""))
    content = " ".join(str(doc.get("content", "")).split()[:60])

    scored: dict[str, float] = {}
    fallback_count = 0
    total_count = 0
    text = f"{title} {title} {content}"  # Titel doppelt gewichtet
    for word in re.findall(r"\b[a-zA-Z]{5,}\b", text.lower()):
        if word in _STOP:
            continue
        total_count += 1
        idf = _IDF_CACHE.get(word, 3.0)
        if word not in _IDF_CACHE:
            fallback_count += 1
        scored[word] = scored.get(word, 0.0) + idf

    if total_count > 0 and fallback_count / total_count > 0.5:
        nouns = _extract_de_nouns(title, str(doc.get("content", "")))
        if nouns:
            chosen = random.sample(nouns, min(3, len(nouns)))
            return " ".join(chosen)

    if not scored:
        return "chaos entropy emergence"

    top = sorted(scored, key=lambda w: -scored[w])[:6]
    chosen = random.sample(top, min(3, len(top)))
    return " ".join(chosen)


# ── Thermisches Pacing ────────────────────────────────────────────────────────


def thermal_delay(temp: float, base: float = 10.0) -> float:
    if temp < 40.0:
        return base
    if temp < 55.0:
        return base + (temp - 40.0) * (base * 0.2)
    return base * 4.0


# ── Distillation-Recorder ─────────────────────────────────────────────────────


@dataclass
class DistillationEntry:
    query: str
    doc_ids: list[str] = field(default_factory=list)
    best_distance: Optional[float] = None
    zone: str = ""
    route: str = ""
    route_score: float = 0.0
    general_hits: int = 0
    code_hits: int = 0
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "doc_ids": self.doc_ids,
            "best_distance": self.best_distance,
            "zone": self.zone,
            "route": self.route,
            "route_score": self.route_score,
            "general_hits": self.general_hits,
            "code_hits": self.code_hits,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }


class DistillationRecorder:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DISTILLATION_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: DistillationEntry) -> None:
        d = entry.to_dict()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def count(self) -> int:
        if not self.path.exists():
            return 0
        return sum(1 for _ in self.path.open())


# ── Autopilot ─────────────────────────────────────────────────────────────────


class Autopilot:
    """Hintergrund-Thread: generiert Queries aus dem Vault, feuert sie durch
    den RetrievalService, zeichnet Distillation-Daten auf.

    retrieval_service: collect2 RetrievalService-Instanz (für .retrieve()).
    interval: Sekunden zwischen Queries.
    max_cycles: maximale Queries bevor der Loop stoppt (0 = endlos).
    """

    def __init__(self, retrieval_service,
                 interval: int = DEFAULT_INTERVAL,
                 max_cycles: int = DEFAULT_MAX_CYCLES,
                 seen_file: Optional[Path] = None,
                 distillation_file: Optional[Path] = None):
        self._rs = retrieval_service
        self.interval = interval
        self.max_cycles = max_cycles

        self._seen_file = Path(seen_file) if seen_file else SEEN_FILE
        self._dist = DistillationRecorder(distillation_file)

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._completed = 0

    # ── Thread-Steuerung ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="Autopilot")
        self._thread.start()
        logger.info("Autopilot gestartet (Intervall=%ds, max=%d)", self.interval, self.max_cycles)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Autopilot gestoppt (%d Zyklen)", self._completed)

    @property
    def completed(self) -> int:
        return self._completed

    # ── Loop ──────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Vault-Docs aus dem General-Searcher
        store = self._rs.general.store
        docs = list(store.archive) if store.archive else []
        if not docs:
            logger.warning("Vault leer — Autopilot pausiert")
            return

        _build_idf(docs)
        doc_lookup = {d.get("id"): d for d in docs}
        seen = _load_seen(self._seen_file)

        for cycle in range(self.max_cycles if self.max_cycles > 0 else 2**31):
            if not self._running:
                break

            unseen = [d for d in docs if d.get("id") not in seen]
            if not unseen:
                _save_seen(seen, self._seen_file)
                logger.info("Alle %d Docs gesehen — Reset", len(seen))
                seen.clear()
                unseen = [d for d in docs]

            seed_doc = random.choice(unseen)
            seed_id = seed_doc.get("id")
            seen.add(seed_id)

            query = extract_query(seed_doc)
            logger.debug("[%d] '%s' ← %s", cycle + 1, query, str(seed_doc.get("title", "?"))[:50])

            try:
                result = self._rs.retrieve(query, top_k=20, max_hits=10)
            except Exception as e:
                logger.warning("Retrieval-Fehler: %s", e)
                time.sleep(self.interval)
                continue

            entry = DistillationEntry(
                query=query,
                doc_ids=[h.doc_id for h in (result.general.hits if result.general else [])],
                best_distance=result.general.best_distance if result.general else None,
                zone=result.general.verdict.zone if result.general and result.general.verdict else "",
                route=result.route,
                route_score=result.route_score,
                general_hits=len(result.general.hits) if result.general else 0,
                code_hits=len(result.code.hits) if result.code else 0,
            )
            self._dist.record(entry)
            self._completed += 1

            z = entry.zone or "?"
            d = f"{entry.best_distance:.0f}" if entry.best_distance is not None else "∞"
            logger.info("[%d] '%s' → %s (Δ=%s) | %d Hits | %d in DB",
                        self._completed, query[:60], z, d,
                        entry.general_hits, self._dist.count())

            if self._completed % 10 == 0:
                _save_seen(seen, self._seen_file)

            time.sleep(self.interval)

        _save_seen(seen, self._seen_file)
        logger.info("Autopilot fertig: %d Zyklen, %d Einträge", self._completed, self._dist.count())

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "running": self._running,
            "completed": self._completed,
            "distillation_entries": self._dist.count(),
            "distillation_file": str(self._dist.path),
            "seen_file": str(self._seen_file),
        }
