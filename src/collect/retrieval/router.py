"""CodeRouter — Query-Klassifikation general/both/code per Embedding-Centroid.

Port aus collect (utils/query_router.py): Cosine-Ähnlichkeit der Query zur
Code-Vault-Centroid. Statt eigenem SentenceTransformer + Init-Thread wird der
geteilte EmbeddingBackend injiziert (testbar mit Fake-Embedder); Schwellen
kommen aus der Config.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from collect.config import settings

logger = logging.getLogger(__name__)

ROUTE_GENERAL = "general"
ROUTE_BOTH = "both"
ROUTE_CODE = "code"


def load_or_compute_centroid(centroid_file: Path, cache_file: Path) -> Optional[np.ndarray]:
    """Centroid laden; fehlt sie, aus dem Embedding-Cache berechnen + persistieren."""
    centroid_file, cache_file = Path(centroid_file), Path(cache_file)
    if centroid_file.exists():
        try:
            return np.load(centroid_file).astype(np.float32)
        except Exception as e:
            logger.warning("Centroid-Load fehlgeschlagen (%s): %s", centroid_file, e)

    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                cache = pickle.load(f)
            if cache:
                vectors = np.stack(list(cache.values())).astype(np.float32)
                centroid = vectors.mean(axis=0)
                norm = np.linalg.norm(centroid)
                if norm > 0:
                    centroid = centroid / norm
                np.save(centroid_file, centroid)
                logger.info("Centroid aus %s Vektoren berechnet → %s",
                            f"{len(cache):,}", centroid_file.name)
                return centroid
        except Exception as e:
            logger.warning("Centroid-Berechnung fehlgeschlagen: %s", e)
    return None


class CodeRouter:
    def __init__(self, embed_fn, centroid: Optional[np.ndarray] = None,
                 high: Optional[float] = None, low: Optional[float] = None):
        """
        embed_fn: (text) -> (dim,) ndarray — z.B. EmbeddingBackend.embed_one
        centroid: normalisierte Code-Centroid; None → Router inaktiv ("general")
        """
        self.embed_fn = embed_fn
        self.centroid = centroid
        self.high = settings.code_route_high if high is None else high
        self.low = settings.code_route_low if low is None else low
        if self.centroid is None:
            logger.warning("Code-Routing inaktiv — keine Centroid verfügbar.")

    @classmethod
    def from_config(cls, embed_fn) -> "CodeRouter":
        centroid = load_or_compute_centroid(
            settings.code_centroid_file, settings.code_cache_file)
        return cls(embed_fn, centroid)

    def classify(self, query: str) -> Tuple[str, float]:
        """→ (route, cosine): 'code' ab high, 'both' ab low, sonst 'general'."""
        if self.centroid is None:
            return (ROUTE_GENERAL, 0.0)
        try:
            vec = np.asarray(self.embed_fn(query), dtype=np.float32).reshape(-1)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            score = float(np.dot(vec, self.centroid))
        except Exception as e:
            logger.error("Routing-Klassifikation fehlgeschlagen: %s", e)
            return (ROUTE_GENERAL, 0.0)

        if score >= self.high:
            return (ROUTE_CODE, score)
        if score >= self.low:
            return (ROUTE_BOTH, score)
        return (ROUTE_GENERAL, score)
