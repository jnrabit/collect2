"""ChaosRetrieval auf dem Mini-Vault: Relevanz, Distanz-Semantik, Feedback."""

import numpy as np

from collect.retrieval.chaos import ChaosRetrieval, RiemannianWarp, ThompsonSampler
from tests.conftest import fake_embedding


def _retriever(mini_store, seed=7):
    # Ohne Engine (Shadow) und ohne Resonanzfeld: deterministisch bis auf
    # Thompson — der ist geseedet.
    return ChaosRetrieval(mini_store, engine=None, field=None, thompson_seed=seed)


def test_search_finds_matching_doc_first(mini_store):
    chaos = _retriever(mini_store)
    results = chaos.search(fake_embedding("TLS negotiates keys"), top_k=4)
    assert len(results) == 4
    assert results[0][0] == "doc-tls"
    # Distanzen aufsteigend? Nicht garantiert (Score-Mix), aber der beste
    # Treffer muss deutlich näher sein als der schlechteste.
    assert results[0][1] < results[-1][1]


def test_distance_semantics_identical_vector(mini_store):
    chaos = _retriever(mini_store)
    best_id, best_dist = chaos.search(fake_embedding("Spark is a cluster engine"), top_k=1)[0]
    assert best_id == "doc-spark"
    # score ~ a*1.0 + kleine Zufallsanteile → dist = (1-score)*100 klar unter TRUST (55)
    assert best_dist < 55.0


def test_top_k_capped_at_corpus_size(mini_store):
    chaos = _retriever(mini_store)
    assert len(chaos.search(fake_embedding("x"), top_k=100)) == 4


def test_empty_store_returns_empty(tmp_path):
    from collect.retrieval.store import VaultStore
    empty = VaultStore(tmp_path / "a.monolith", tmp_path / "b.pkl")
    assert ChaosRetrieval(empty).search(fake_embedding("x")) == []


def test_thompson_feedback_shifts_beta():
    ts = ThompsonSampler(seed=1)
    ts.update(["a", "b"], relevant={"a"})
    assert ts.alpha["a"] == 2.0 and ts.beta["a"] == 1.0
    assert ts.alpha["b"] == 1.0 and ts.beta["b"] == 1.3


def test_warp_projection_is_deterministic():
    w1, w2 = RiemannianWarp(), RiemannianWarp()
    assert np.allclose(w1._P, w2._P)  # Seed 1337 fixiert


def test_diagnostics_shape(mini_store):
    chaos = _retriever(mini_store)
    chaos.search(fake_embedding("x"), top_k=2)
    d = chaos.diagnostics()
    assert d["search_count"] == 1
    assert "warp_divergence" in d and "thompson" in d
