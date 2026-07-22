"""OsmosisObserver — Topologie-Radar (Osmose | Vakuum | Kristallisation).

Passiver Beobachter der emergenten Cluster-Struktur. Nutzt die
ResonanceField.emergent_clusters als "Festland" und misst, wie sich
Dokument-Populationen um diese Gravitationszentren verschieben.

Drei Ereignis-Typen:
  OSMOSE        — Dokument-Wanderung zwischen Clustern (>15% Δ)
  VAKUUM        — Cluster-Kollaps (Population → 0)
  KRISTALLISATION — Dichtespitze (>0.85 Cosine-Score im Cluster)

Läuft als Hintergrund-Thread, schreibt append-only nach
data/osmosis_log.jsonl.

Port aus ai_neu/osmosis_observer.py; reduziert auf collect2-Kern.
"""

from __future__ import annotations

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

DATA_DIR = settings.data_dir
LOG_FILE = DATA_DIR / "osmosis_log.jsonl"
SCAN_INTERVAL_SEC = 30
EMBED_DIM = settings.embedding_dim
VORONOI_MIN_SCORE = 0.05        # Mindest-Cosine für Cluster-Zuordnung
OSMOSE_FRACTION = 0.15           # Δ > 15% → Osmose-Ereignis
CRYSTAL_DENSITY_THRESHOLD = 0.85
HISTORY_LEN = 30


# ── OsmosisObserver ───────────────────────────────────────────────────────────


class OsmosisObserver:
    """Passiver Beobachter der Cluster-Topologie.

    chaos_retrieval: ChaosRetrieval-Instanz (liefert .warp, .store, .field).
    interval: Sekunden zwischen Scans.
    log_file: Ausgabepfad (default: data_dir/osmosis_log.jsonl).
    """

    def __init__(self, chaos_retrieval,
                 interval: int = SCAN_INTERVAL_SEC,
                 log_file: Optional[Path] = None):
        self._cr = chaos_retrieval
        self.interval = interval
        self.log_file = Path(log_file) if log_file else LOG_FILE

        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Einmalig geladene Basis-Daten
        self._doc_matrix: Optional[np.ndarray] = None
        self._id_map: list[str] = []
        self._cluster_ids: list[str] = []
        self._cluster_vecs: Optional[np.ndarray] = None
        self._initialized = False

        # Zustand zwischen Scans
        self._prev_populations: dict[str, int] = {}
        self._history: deque[dict] = deque(maxlen=HISTORY_LEN)
        self.scan_count = 0
        self.osmosis_total = 0
        self.vacuum_total = 0
        self.crystal_total = 0

    # ── Thread-Steuerung ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._init_static_universe()
        if not self._initialized:
            logger.warning("OsmosisObserver: Keine Cluster — pausiert")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="OsmosisObserver",
        )
        self._thread.start()
        logger.info("OsmosisObserver gestartet: %d Docs, %d Cluster",
                    len(self._id_map), len(self._cluster_ids))

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("OsmosisObserver gestoppt")

    # ── Initialisierung ───────────────────────────────────────────────────────

    def _init_static_universe(self) -> None:
        store = self._cr.store
        field = self._cr.field

        if store.matrix is None or not store.id_map:
            return
        self._doc_matrix = store.matrix.copy()
        self._id_map = list(store.id_map)

        if field is None:
            return

        cluster_vecs = []
        cluster_ids = []

        # emergent_clusters sind Listen von Doc-IDs — nimm erste als Anker-Vektor
        for cluster in (field.emergent_clusters or []):
            if isinstance(cluster, list) and cluster:
                anchor_id = str(cluster[0])
            elif isinstance(cluster, str):
                anchor_id = cluster
            else:
                continue
            vec = store.doc_cache.get(anchor_id)
            if vec is not None:
                cluster_vecs.append(vec.astype(np.float32))
                cluster_ids.append(anchor_id)

        if cluster_vecs:
            self._cluster_vecs = np.stack(cluster_vecs)
            self._cluster_ids = cluster_ids
            # init prev_populations via ersten Scan
            pops, _, _ = self._voronoi(self._cluster_vecs, self._doc_matrix, self._id_map)
            self._prev_populations = pops
            self._initialized = True
            logger.info("Osmosis: %d Cluster kartiert", len(cluster_ids))
        else:
            logger.info("Osmosis: keine Cluster-Vektoren — warte auf Emergenz")

    # ── Loop ──────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._scan()
            except Exception as e:
                self._write({
                    "type": "ERROR", "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": str(e),
                })
            time.sleep(self.interval)

    def _scan(self) -> None:
        store = self._cr.store
        if store.matrix is None:
            return

        doc_matrix = store.matrix
        id_map = list(store.id_map)

        # Bei Matrix-Änderung (neue Docs durch DreamCycle): neu initialisieren
        if len(id_map) != len(self._id_map):
            logger.debug("Osmosis: Matrix gewachsen (%d→%d) — re-init",
                         len(self._id_map), len(id_map))
            self._init_static_universe()
            return

        if self._cluster_vecs is None:
            return

        pops, densities, _ = self._voronoi(self._cluster_vecs, doc_matrix, id_map)
        self.scan_count += 1

        osmosis, vacuum, crystal = self._detect_events(pops, densities)

        if osmosis or vacuum or crystal:
            self._write({
                "type": "TOPOLOGY_SHIFT",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "scan": self.scan_count,
                "anomalies": {
                    "osmosis": osmosis,
                    "vacuum": vacuum,
                    "crystal": crystal,
                },
            })
            self._history.append({"scan": self.scan_count,
                                  "osmosis": len(osmosis),
                                  "vacuum": len(vacuum),
                                  "crystal": len(crystal)})
            logger.debug("Osmosis S%d: O:%d V:%d K:%d",
                         self.scan_count, len(osmosis), len(vacuum), len(crystal))

        self._prev_populations = pops
        self.osmosis_total += len(osmosis)
        self.vacuum_total += len(vacuum)
        self.crystal_total += len(crystal)

    # ── Voronoi-Zuordnung ─────────────────────────────────────────────────────

    @staticmethod
    def _voronoi(cluster_vecs: np.ndarray, doc_matrix: np.ndarray, id_map: list[str]
                 ) -> tuple[dict[str, int], dict[str, float], dict[str, list[str]]]:
        # Cosine-Similarity: Cluster × Docs
        c_norm = cluster_vecs / (np.linalg.norm(cluster_vecs, axis=1, keepdims=True) + 1e-9)
        d_norm = doc_matrix / (np.linalg.norm(doc_matrix, axis=1, keepdims=True) + 1e-9)
        scores = c_norm @ d_norm.T  # (n_clusters, n_docs)

        assigned = np.argmax(scores, axis=0)
        max_scores = np.max(scores, axis=0)

        pops: dict[str, int] = {str(cid): 0 for cid in id_map[:len(cluster_vecs)]}
        densities: dict[str, float] = {}
        members: dict[str, list[str]] = {}

        n_clusters = len(cluster_vecs)
        for c_idx in range(n_clusters):
            mask = (assigned == c_idx) & (max_scores > VORONOI_MIN_SCORE)
            count = int(np.sum(mask))
            pops[str(c_idx)] = count
            if count > 0:
                densities[str(c_idx)] = float(np.mean(max_scores[mask]))
                members[str(c_idx)] = [id_map[i] for i in np.where(mask)[0][:20]]
            else:
                densities[str(c_idx)] = 0.0
                members[str(c_idx)] = []

        return pops, densities, members

    # ── Ereignis-Detektion ────────────────────────────────────────────────────

    def _detect_events(self, pops: dict[str, int], densities: dict[str, float]
                       ) -> tuple[list[dict], list[dict], list[dict]]:
        osmosis: list[dict] = []
        vacuum: list[dict] = []
        crystal: list[dict] = []

        for c_idx in pops:
            old = self._prev_populations.get(c_idx, 0)
            new = pops[c_idx]
            delta = new - old

            if new == 0 and old > 0:
                vacuum.append({"cluster": c_idx, "dropped": old})
            elif old > 0 and delta < -int(old * OSMOSE_FRACTION) and new > 0:
                osmosis.append({"cluster": c_idx, "flow": "OUT",
                                "amount": abs(delta), "delta_pct": round(abs(delta) / max(old, 1) * 100, 1)})
            elif old > 0 and delta > int(old * OSMOSE_FRACTION):
                osmosis.append({"cluster": c_idx, "flow": "IN",
                                "amount": delta, "delta_pct": round(delta / max(old, 1) * 100, 1)})

            d = densities.get(c_idx, 0.0)
            if d > CRYSTAL_DENSITY_THRESHOLD:
                crystal.append({"cluster": c_idx, "density": round(d, 4), "pop": new})

        return osmosis, vacuum, crystal

    # ── Log ───────────────────────────────────────────────────────────────────

    def _write(self, entry: dict) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── Abfrage ───────────────────────────────────────────────────────────────

    def recent(self, n: int = 5) -> list[dict]:
        return list(self._history)[-n:]

    def status(self) -> dict:
        return {
            "running": self._running,
            "initialized": self._initialized,
            "scan_count": self.scan_count,
            "osmosis_total": self.osmosis_total,
            "vacuum_total": self.vacuum_total,
            "crystal_total": self.crystal_total,
            "clusters": len(self._cluster_ids),
            "docs": len(self._id_map),
            "log_file": str(self.log_file),
        }


# ── Log-Analyse ───────────────────────────────────────────────────────────────


def analyze_osmosis_log(log_file: Optional[Path] = None) -> dict:
    path = Path(log_file) if log_file else LOG_FILE
    if not path.exists():
        return {"error": f"Kein Log: {path}"}

    total_scans = 0
    total_osmosis = 0
    total_vacuum = 0
    total_crystal = 0

    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") != "TOPOLOGY_SHIFT":
                continue
            total_scans += 1
            a = e.get("anomalies", {})
            total_osmosis += len(a.get("osmosis", []))
            total_vacuum += len(a.get("vacuum", []))
            total_crystal += len(a.get("crystal", []))

    return {
        "total_scans": total_scans,
        "total_osmosis_events": total_osmosis,
        "total_vacuum_events": total_vacuum,
        "total_crystal_events": total_crystal,
        "log_file": str(path),
    }
