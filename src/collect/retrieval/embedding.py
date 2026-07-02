"""EmbeddingBackend — DAS eine kanonische Embedding-API (DESIGN.md §5.6).

Im Alt-System existierten zwei divergente APIs (`embed_sync` vs `encode`),
was zu Laufzeit-Crashes führte. Hier gibt es genau eine Klasse mit genau
einer Methode `embed(texts) -> np.ndarray (n, dim) float32`, unnormalisiert
(kompatibel zu den bestehenden Embedding-Caches). Modell wird lazy geladen;
Device kommt aus der Config (cpu default — VRAM gehört der LLM).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional, Sequence

import numpy as np

from collect.config import settings

logger = logging.getLogger(__name__)


class EmbeddingBackend:
    def __init__(self, model_name: Optional[str] = None, device: Optional[str] = None):
        self.model_name = model_name or settings.embedding_model
        self.device = device or settings.embedding_device
        self.dim = settings.embedding_dim
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer
                    logger.info("Lade Embedding-Modell %s auf %s …",
                                self.model_name, self.device)
                    self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def embed(self, texts: str | Sequence[str], normalize: bool = False) -> np.ndarray:
        """Immer (n, dim) float32 — auch bei einem einzelnen String."""
        if isinstance(texts, str):
            texts = [texts]
        vecs = self._load().encode(list(texts), convert_to_numpy=True)
        vecs = np.atleast_2d(vecs).astype(np.float32)
        if normalize:
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            vecs = vecs / np.maximum(norms, 1e-12)
        return vecs

    def embed_one(self, text: str, normalize: bool = False) -> np.ndarray:
        """(dim,) float32 für Einzel-Queries."""
        return self.embed(text, normalize=normalize)[0]


# Prozessweite Instanz (ein Modell im Speicher, alle Nutzer teilen es)
_default: Optional[EmbeddingBackend] = None
_default_lock = threading.Lock()


def get_backend() -> EmbeddingBackend:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = EmbeddingBackend()
    return _default


# Typ für injizierbare Embed-Funktionen in Tests: (text) -> (dim,) ndarray
EmbedFn = Callable[[str], np.ndarray]
