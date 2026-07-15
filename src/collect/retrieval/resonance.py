"""ResonanceField — emergente Ko-Aktivierungsmatrix als funktionale Kraft.

Port aus vibelike (intelligence/resonance.py); Verhalten unverändert, aber
field_file wird injiziert statt global importiert. Muster aus wiederholtem
gemeinsamem Retrieval erzeugen Gravitationszentren im Lorenz-Phasenraum und
verbiegen zukünftige Suchen.
"""

from __future__ import annotations

import logging
import pickle
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ResonanceField:
    def __init__(self, field_file: Path, n_lorenz_dims: int = 8,
                 decay: Optional[float] = None):
        from collect.config import settings
        self.field_file = Path(field_file)
        self.n_lorenz_dims = n_lorenz_dims
        # None → Config (COLLECT_RESONANCE_DECAY); expliziter Wert gewinnt
        self.decay = settings.resonance_decay if decay is None else decay
        self.R: dict = defaultdict(lambda: defaultdict(float))
        self.doc_positions: dict = {}
        self.gravity_centers: list = []
        self.emergent_clusters: list = []
        self.query_count = 0
        self.last_pattern_update = 0
        self._projection: Optional[np.ndarray] = None
        self._load()

    # ── Dokument-Registration ────────────────────────────────────────────

    def register_documents(self, doc_embeddings: dict) -> None:
        if not doc_embeddings:
            return
        embed_dim = next(iter(doc_embeddings.values())).shape[0]
        # Re-Projection bei fehlender Projektion oder Dim-Mismatch (z.B. nach
        # Lorenz-Dim-Migration in _load).
        if (self._projection is None
                or self._projection.shape[1] != embed_dim
                or self._projection.shape[0] != self.n_lorenz_dims):
            rng = np.random.RandomState(42)
            P = rng.randn(self.n_lorenz_dims, embed_dim).astype(np.float32)
            norms = np.linalg.norm(P, axis=1, keepdims=True)
            self._projection = P / norms

        new = 0
        for doc_id, vec in doc_embeddings.items():
            if doc_id not in self.doc_positions:
                pos = self._projection @ vec.astype(np.float32)
                pos = pos / (np.linalg.norm(pos) + 1e-8) * 20.0
                self.doc_positions[doc_id] = pos
                new += 1
        if new:
            logger.info("Resonanzfeld: %d neue Docs kartiert (%s gesamt)",
                        new, f"{len(self.doc_positions):,}")

    # ── Ko-Aktivierung aufzeichnen ───────────────────────────────────────

    def record_activation(self, retrieved_ids: list, query_vec: np.ndarray = None) -> None:
        self.query_count += 1
        if self.query_count % 10 == 0:
            self._decay()

        n = len(retrieved_ids)
        for i in range(n):
            for j in range(i + 1, n):
                ia, ib = retrieved_ids[i], retrieved_ids[j]
                pos_bonus = 1.0
                if ia in self.doc_positions and ib in self.doc_positions:
                    d = np.linalg.norm(self.doc_positions[ia] - self.doc_positions[ib])
                    pos_bonus = 1.0 / (1.0 + d * 0.1)
                boost = (1.0 / (i + 1)) * (1.0 / (j + 1)) * pos_bonus
                self.R[ia][ib] += boost
                self.R[ib][ia] += boost

        if self.query_count - self.last_pattern_update >= 50:
            self._detect_clusters()
            self._rebuild_gravity()
            self.last_pattern_update = self.query_count
            self.save()

    # ── Resonanz-Boost für Kandidaten ────────────────────────────────────

    def get_resonance_boost(self, candidate_ids: list, anchor_ids: list) -> dict:
        # Iteriert über sparse R[aid]-Einträge statt über alle Kandidaten
        boosts = defaultdict(float)
        id_set = set(candidate_ids)
        for aid in anchor_ids:
            if aid in self.R:
                for cid, val in self.R[aid].items():
                    if cid in id_set:
                        boosts[cid] += val
        return dict(boosts)

    # ── Lorenz-Gravitationskraft ─────────────────────────────────────────

    def get_lorenz_force(self, lorenz_pos: np.ndarray) -> np.ndarray:
        if not self.gravity_centers:
            return np.zeros(self.n_lorenz_dims)
        force = np.zeros(self.n_lorenz_dims)
        for center, strength, _ in self.gravity_centers:
            delta = center - lorenz_pos
            dist = np.linalg.norm(delta) + 1e-8
            force += (delta / dist) * (strength / (dist ** 1.5 + 2.0))
        norm = np.linalg.norm(force)
        if norm > 2.0:
            force = force / norm * 2.0
        return force

    # ── Cluster-Erkennung (Flood-Fill) ───────────────────────────────────

    def _detect_clusters(self) -> None:
        threshold = self._adaptive_threshold()
        adjacency = defaultdict(set)
        for ia, neighbors in self.R.items():
            for ib, w in neighbors.items():
                if w > threshold:
                    adjacency[ia].add(ib)
                    adjacency[ib].add(ia)

        visited, clusters = set(), []
        for start in adjacency:
            if start in visited:
                continue
            cluster, queue = [], [start]
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                cluster.append(node)
                queue.extend(adjacency[node] - visited)
            if len(cluster) >= 3:
                clusters.append(cluster)

        self.emergent_clusters = clusters
        if clusters:
            logger.info("%d Cluster erkannt (Größen: %s)", len(clusters),
                        sorted([len(c) for c in clusters], reverse=True)[:5])

    def _adaptive_threshold(self) -> float:
        weights = [w for nbrs in self.R.values() for w in nbrs.values()]
        return float(np.percentile(weights, 85)) if weights else 0.1

    def _rebuild_gravity(self) -> None:
        self.gravity_centers = []
        for cluster in self.emergent_clusters:
            positions = [self.doc_positions[d] for d in cluster if d in self.doc_positions]
            if not positions:
                continue
            center = np.mean(positions, axis=0)
            weights = [self.R[i][j] for i in cluster for j in cluster
                       if i != j and j in self.R.get(i, {})]
            strength = np.mean(weights) * np.log1p(len(cluster)) if weights else 0.0
            self.gravity_centers.append((center, float(strength), cluster))
        logger.info("%d Gravitationszentren aktiv", len(self.gravity_centers))

    def _decay(self) -> None:
        for ia in list(self.R):
            for ib in list(self.R[ia]):
                self.R[ia][ib] *= self.decay
                if self.R[ia][ib] < 0.001:
                    del self.R[ia][ib]
            if not self.R[ia]:
                del self.R[ia]

    def get_stats(self) -> dict:
        return {
            "queries": self.query_count,
            "tracked_pairs": sum(len(v) for v in self.R.values()) // 2,
            "emergent_clusters": len(self.emergent_clusters),
            "gravity_centers": len(self.gravity_centers),
            "cluster_sizes": sorted([len(c) for c in self.emergent_clusters], reverse=True)[:10],
        }

    # ── Persistenz ───────────────────────────────────────────────────────

    def save(self) -> None:
        state = {
            "R": {k: dict(v) for k, v in self.R.items()},
            "doc_positions": self.doc_positions,
            "gravity_centers": self.gravity_centers,
            "emergent_clusters": self.emergent_clusters,
            "query_count": self.query_count,
            "last_pattern_update": self.last_pattern_update,
            "_projection": self._projection,
        }
        try:
            self.field_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.field_file, "wb") as f:
                pickle.dump(state, f)
        except OSError as e:
            logger.warning("Resonanzfeld-Save fehlgeschlagen: %s", e)

    def _load(self) -> None:
        if not self.field_file.exists():
            return
        try:
            with open(self.field_file, "rb") as f:
                s = pickle.load(f)
            self.R = defaultdict(lambda: defaultdict(float))
            for k, v in s.get("R", {}).items():
                self.R[k] = defaultdict(float, v)
            self.doc_positions = s.get("doc_positions", {})
            self.gravity_centers = s.get("gravity_centers", [])
            self.emergent_clusters = s.get("emergent_clusters", [])
            self.query_count = s.get("query_count", 0)
            self.last_pattern_update = s.get("last_pattern_update", 0)
            self._projection = s.get("_projection")

            # Schema-Drift (Lorenz-Dim-Migration): Ko-Aktivierungen R sind
            # dimension-agnostisch und bleiben; die Geometrie wird verworfen
            # und beim nächsten register_documents() neu aufgebaut.
            if (self._projection is not None
                    and self._projection.shape[0] != self.n_lorenz_dims):
                old_dim = self._projection.shape[0]
                backup_path = f"{self.field_file}.broken-{int(time.time())}"
                try:
                    shutil.copy(self.field_file, backup_path)
                except OSError as bu_err:
                    logger.warning("Backup nach %s fehlgeschlagen: %s", backup_path, bu_err)

                self._projection = None
                self.doc_positions = {}
                self.gravity_centers = []
                self.emergent_clusters = []

                logger.warning(
                    "Resonanzfeld: %dD→%dD Migration — Geometrie verworfen, "
                    "R + query_count behalten. Backup: %s",
                    old_dim, self.n_lorenz_dims, backup_path)
                # Sofort persistieren — sonst migriert jeder Restart erneut.
                self.save()
            else:
                logger.info("Resonanzfeld: %d Queries, %d Cluster",
                            self.query_count, len(self.emergent_clusters))
        except Exception as e:
            logger.warning("Resonanzfeld reset: %s", e)
