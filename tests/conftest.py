"""Geteilte Fixtures: synthetischer Mini-Vault mit deterministischen Embeddings."""

import pickle

import numpy as np
import pytest

from collect.retrieval.store import VaultStore
from collect.retrieval.vault import Vault

DIM = 384


def fake_embedding(text: str) -> np.ndarray:
    """Deterministischer Pseudo-Embedder: gleicher Text → gleicher Vektor."""
    rng = np.random.RandomState(abs(hash(text)) % (2 ** 31))
    v = rng.randn(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


class FakeEmbedder:
    """Drop-in für EmbeddingBackend in Tests (kein torch nötig)."""

    def embed(self, texts, normalize=False):
        if isinstance(texts, str):
            texts = [texts]
        return np.stack([fake_embedding(t) for t in texts])

    def embed_one(self, text, normalize=False):
        return fake_embedding(text)


DOCS = [
    {"id": "doc-spark", "title": "Apache Spark", "content": "Spark is a cluster engine",
     "source": "wiki"},
    {"id": "doc-tls", "title": "TLS Handshake", "content": "TLS negotiates keys",
     "source": "rfc"},
    {"id": "doc-py", "title": "Python GIL", "content": "The GIL serializes threads",
     "source": "wiki"},
    {"id": "doc-noise", "title": "Noise", "content": "unrelated filler text",
     "source": "misc"},
]


@pytest.fixture
def mini_store(tmp_path) -> VaultStore:
    """VaultStore mit 4 Docs, deren Cache-Vektoren zu fake_embedding passen:
    die Suche nach einem Dokument-Content muss genau dieses Doc finden."""
    vault_file = tmp_path / "mini.monolith"
    Vault(vault_file).save(DOCS)

    cache = {d["id"]: fake_embedding(d["content"]) for d in DOCS}
    cache_file = tmp_path / "mini_cache.pkl"
    with open(cache_file, "wb") as f:
        pickle.dump(cache, f)

    return VaultStore(vault_file, cache_file)
