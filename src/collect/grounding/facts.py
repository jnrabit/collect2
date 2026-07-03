"""FactGrounder — verbürgte Ossifikat-Fakten als autoritatives Grounding.

Port von vibelikes `_confirmed_facts`: bestätigte (nicht-retractete) Tripel
werden per Cosine zur Query gewählt und der Antwort-Synthese VORANGESTELLT —
bei Widerspruch haben sie Vorrang vor Vault-Quellen. Der Store wird pro Query
frisch gelesen (neu Verbürgtes greift sofort); Embeddings sind gecacht.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from collect.config import settings

logger = logging.getLogger(__name__)


class FactGrounder:
    def __init__(self, embed_fn, db_path: Optional[Path] = None):
        """embed_fn: (text) -> (dim,) ndarray — z.B. EmbeddingBackend.embed_one"""
        self.embed_fn = embed_fn
        self.db_path = Path(db_path or settings.ossifikat_db)
        self._vec_cache: dict[str, np.ndarray] = {}

    def _load_confirmed(self) -> list:
        if not self.db_path.exists():
            return []
        try:
            from ossifikat.store import OssifikatStore
            store = OssifikatStore(str(self.db_path))
            try:
                return store.query()  # bestätigte, nicht-retractete Tripel
            finally:
                store.close()
        except Exception as e:
            logger.warning("Ossifikat-Store nicht lesbar (%s): %s", self.db_path, e)
            return []

    def relevant_facts(self, query: str, k: Optional[int] = None,
                       max_dist: Optional[float] = None) -> list[dict]:
        """Top-k verbürgte Fakten nahe der Query. [] wenn deaktiviert/leer."""
        if not settings.ground_on_facts:
            return []
        rows = self._load_confirmed()
        if not rows:
            return []
        k = k or settings.fact_top_k
        max_dist = settings.fact_max_distance if max_dist is None else max_dist

        q = np.asarray(self.embed_fn(query), dtype=np.float32).reshape(-1)
        q = q / (np.linalg.norm(q) + 1e-8)

        vecs = []
        for t in rows:
            key = f"{t.subject}|{t.predicate}|{t.object}"
            v = self._vec_cache.get(key)
            if v is None:
                v = np.asarray(self.embed_fn(f"{t.subject} {t.predicate} {t.object}"),
                               dtype=np.float32).reshape(-1)
                v = v / (np.linalg.norm(v) + 1e-8)
                self._vec_cache[key] = v
            vecs.append(v)

        sims = np.stack(vecs) @ q
        out = []
        for i in np.argsort(-sims)[:k]:
            dist = float(1.0 - sims[i])
            if dist > max_dist:
                continue
            t = rows[i]
            out.append({
                "id": t.id,
                "content": f"{t.subject} —[{t.predicate}]→ {t.object}",
                "subject": t.subject,
                "predicate": t.predicate,
                "object": t.object,
                "distance": dist,
                "source": "ossifikat",
                "vault": "verbürgt",
            })
        if out:
            logger.info("%d verbürgte(r) Fakt(en) als Grounding", len(out))
        return out
