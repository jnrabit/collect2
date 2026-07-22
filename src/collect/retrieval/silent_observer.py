"""SilentObserver — passiver Beobachter der Warp-Geometrie (Phantom | Void | Drift).

Greift NUR lesend auf ChaosRetrieval und NativeEngine zu. Kein Query,
kein Intent, kein Eingriff. Läuft als Hintergrund-Thread, schreibt
append-only nach data/silent_observer.jsonl.

Drei Modi:
  PHANTOM   — leerer Query-Vektor: welche Docs berührt der Warp ohne Frage?
  VOID      — invertierter Vektor: was wird strukturell gemieden?
  DRIFT     — Delta zwischen Phantom-Scans: Eigenbewegung des Systems

Port aus ai_neu/observer.py; API an collect2-Konventionen angepasst
(DESIGN.md §5: Transport/Logik getrennt, collect.*-Imports, <400 LOC).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from collect.config import settings

logger = logging.getLogger(__name__)

LOG_FILE = settings.data_dir / "silent_observer.jsonl"
EMBED_DIM = settings.embedding_dim
SCAN_INTERVAL_SEC = 60
PHANTOM_THRESHOLD = 0.15
VOID_THRESHOLD = -0.10
DRIFT_THRESHOLD = 0.08
HISTORY_LEN = 20
MAX_ANOMALIES = 20


# ── Attraktor-Signatur ────────────────────────────────────────────────────────


def attractor_signature(lorenz_state: dict) -> str:
    keys = ["x1", "y1", "z1", "w1", "x2", "y2", "z2", "w2"]
    vals = [round(lorenz_state.get(k, 0.0), 4) for k in keys]
    raw = json.dumps(vals, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ── Scan-Logik (reine Funktionen, unabhängig vom Observer-Objekt testbar) ─────


def _phantom(doc_matrix: np.ndarray, id_map: list, warp,
             threshold: float = PHANTOM_THRESHOLD,
             embed_dim: int = EMBED_DIM) -> list[dict]:
    empty = np.zeros(embed_dim, dtype=np.float32)
    scores = warp.score(empty, doc_matrix)
    hits = []
    for i, s in enumerate(scores):
        if s > threshold:
            hits.append({"doc_id": id_map[i], "score": float(s), "mode": "PHANTOM"})
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:MAX_ANOMALIES]


def _void(doc_matrix: np.ndarray, id_map: list, warp,
          threshold: float = VOID_THRESHOLD,
          embed_dim: int = EMBED_DIM) -> list[dict]:
    inv = np.full(embed_dim, -1.0 / np.sqrt(embed_dim), dtype=np.float32)
    scores = warp.score(inv, doc_matrix)
    voids = []
    for i, s in enumerate(scores):
        if s < threshold:
            voids.append({"doc_id": id_map[i], "score": float(s), "mode": "VOID"})
    voids.sort(key=lambda x: x["score"])
    return voids[:MAX_ANOMALIES]


def _drift(prev_scores: Optional[np.ndarray], curr_scores: np.ndarray,
           id_map: list, threshold: float = DRIFT_THRESHOLD) -> list[dict]:
    if prev_scores is None or len(prev_scores) != len(curr_scores):
        return []
    delta = curr_scores - prev_scores
    drifts = []
    for i, d in enumerate(delta):
        if abs(d) > threshold:
            drifts.append({
                "doc_id": id_map[i], "delta": float(d),
                "direction": "→" if d > 0 else "←", "mode": "DRIFT",
            })
    drifts.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return drifts[:MAX_ANOMALIES]


# ── Observer-Objekt ───────────────────────────────────────────────────────────


class SilentObserver:
    """Passiver Hintergrund-Beobachter. Greift lesend auf ChaosRetrieval zu.

    chaos_retrieval: ChaosRetrieval-Instanz (liefert .warp, .store.matrix/id_map,
                     .engine für Hardware-State).
    interval: Sekunden zwischen Scans.
    log_file: Ausgabepfad (default: data_dir/silent_observer.jsonl).
    """

    def __init__(self, chaos_retrieval,
                 interval: int = SCAN_INTERVAL_SEC,
                 log_file: Optional[Path] = None):
        self._cr = chaos_retrieval
        self.interval = interval
        self.log_file = Path(log_file) if log_file else LOG_FILE

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._history: deque[dict] = deque(maxlen=HISTORY_LEN)
        self._prev_phantom_scores: Optional[np.ndarray] = None

        self.scan_count = 0
        self.phantom_total = 0
        self.void_total = 0
        self.drift_total = 0

    # ── Thread-Steuerung ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="SilentObserver",
        )
        self._thread.start()
        logger.info("SilentObserver gestartet (Intervall=%ds)", self.interval)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("SilentObserver gestoppt")

    # ── Haupt-Loop ────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._observe()
            except Exception as e:
                self._write_log({
                    "type": "ERROR", "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": str(e),
                })
            time.sleep(self.interval)

    def _observe(self) -> None:
        store = self._cr.store
        if store.matrix is None or not store.id_map:
            return
        doc_matrix, id_map = store.matrix, store.id_map
        warp = self._cr.warp

        lorenz_state = {}
        if self._cr.engine and self._cr.engine.active:
            lorenz_state = self._cr.engine.get_hardware_state() or {}

        sig = attractor_signature(lorenz_state)
        ts = datetime.now(timezone.utc).isoformat()

        phantom_hits = _phantom(doc_matrix, id_map, warp)
        void_hits = _void(doc_matrix, id_map, warp)
        curr_scores = warp.score(np.zeros(EMBED_DIM, dtype=np.float32), doc_matrix)
        drift_hits = _drift(self._prev_phantom_scores, curr_scores, id_map)
        self._prev_phantom_scores = curr_scores.copy()

        self.scan_count += 1
        self.phantom_total += len(phantom_hits)
        self.void_total += len(void_hits)
        self.drift_total += len(drift_hits)

        entry = {
            "type": "OBSERVATION",
            "timestamp": ts,
            "scan": self.scan_count,
            "attractor_sig": sig,
            "lorenz": {
                "entropy": lorenz_state.get("entropy", 0.0),
                "temp": lorenz_state.get("temperature", 0.0),
                "delta_v": lorenz_state.get("delta_v", 0.0),
                "collapse": lorenz_state.get("collapse", False),
            },
            "counts": {
                "phantom": len(phantom_hits), "void": len(void_hits),
                "drift": len(drift_hits),
            },
            "anomalies": phantom_hits + void_hits + drift_hits,
        }
        self._history.append(entry)
        self._write_log(entry)

        if phantom_hits or void_hits or drift_hits:
            logger.debug("%s P:%d V:%d D:%d H=%.2f", sig[:8],
                         len(phantom_hits), len(void_hits), len(drift_hits),
                         float(lorenz_state.get("entropy", 0)))

    # ── Log ───────────────────────────────────────────────────────────────────

    def _write_log(self, entry: dict) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── Abfrage ───────────────────────────────────────────────────────────────

    def recent(self, n: int = 5) -> list[dict]:
        return list(self._history)[-n:]

    def most_touched(self, mode: str = "PHANTOM", top: int = 10) -> list[tuple]:
        counts: dict[str, int] = {}
        for obs in self._history:
            for a in obs.get("anomalies", []):
                if a.get("mode") == mode:
                    counts[a["doc_id"]] = counts.get(a["doc_id"], 0) + 1
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top]

    def signature_history(self) -> list[str]:
        return [obs["attractor_sig"] for obs in self._history
                if obs.get("type") == "OBSERVATION"]

    def status(self) -> dict:
        return {
            "running": self._running,
            "scan_count": self.scan_count,
            "phantom_total": self.phantom_total,
            "void_total": self.void_total,
            "drift_total": self.drift_total,
            "log_file": str(self.log_file),
            "history_len": len(self._history),
        }


# ── Log-Analyse (Standalone, ohne laufendes System) ───────────────────────────


def analyze_log(log_file: Optional[Path] = None) -> dict:
    path = Path(log_file) if log_file else LOG_FILE
    if not path.exists():
        return {"error": f"Kein Log: {path}"}

    phantom_counts: dict[str, int] = {}
    void_counts: dict[str, int] = {}
    drift_counts: dict[str, int] = {}
    signatures: list[str] = []
    total_scans = 0

    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "OBSERVATION":
                continue
            total_scans += 1
            signatures.append(str(entry.get("attractor_sig", "")))
            for a in entry.get("anomalies", []):
                did = str(a.get("doc_id", "?"))
                mode = a.get("mode", "")
                if mode == "PHANTOM":
                    phantom_counts[did] = phantom_counts.get(did, 0) + 1
                elif mode == "VOID":
                    void_counts[did] = void_counts.get(did, 0) + 1
                elif mode == "DRIFT":
                    drift_counts[did] = drift_counts.get(did, 0) + 1

    sig_counts: dict[str, int] = {}
    for s in signatures:
        sig_counts[s] = sig_counts.get(s, 0) + 1
    recurring = {s: c for s, c in sig_counts.items() if c > 1}

    def top(d: dict, n: int = 10) -> list:
        return sorted(d.items(), key=lambda x: x[1], reverse=True)[:n]

    return {
        "total_scans": total_scans,
        "top_phantom": top(phantom_counts),
        "top_void": top(void_counts),
        "top_drift": top(drift_counts),
        "recurring_signatures": recurring,
        "signature_count": len(sig_counts),
    }
