"""RetrievalService End-to-End (mit Fakes, ohne torch/Ollama) + RRF."""

import numpy as np
import pytest

from collect.retrieval.router import CodeRouter
from collect.retrieval.service import RetrievalService, VaultSearcher, rrf_merge
from collect.retrieval.zones import ZONE_FALLBACK
from tests.conftest import DOCS, FakeEmbedder, fake_embedding


# ── RRF ──────────────────────────────────────────────────────────────────

def test_rrf_single_list_passthrough():
    ranking = [("a", 10.0), ("b", 20.0)]
    assert rrf_merge([ranking]) == ranking


def test_rrf_doc_in_both_lists_wins():
    l1 = [("a", 30.0), ("b", 40.0), ("c", 50.0)]
    l2 = [("c", 35.0), ("a", 45.0), ("d", 55.0)]
    merged = rrf_merge([l1, l2])
    ids = [d for d, _ in merged]
    # a: Rang 1+2, c: Rang 3+1 → beide vor b (Rang 2 einmal) und d
    assert set(ids[:2]) == {"a", "c"}
    # Distanz = Minimum über Listen
    dist = dict(merged)
    assert dist["a"] == 30.0 and dist["c"] == 35.0


# ── Service ──────────────────────────────────────────────────────────────

def _searcher(mini_store):
    s = VaultSearcher.__new__(VaultSearcher)  # ohne __init__: Fakes einsetzen
    s.store = mini_store
    s.engine = None
    s.field = None
    from collect.retrieval.chaos import ChaosRetrieval
    s.chaos = ChaosRetrieval(mini_store, engine=None, field=None, thompson_seed=3)
    return s


def _service(mini_store, centroid=None):
    emb = FakeEmbedder()
    router = CodeRouter(emb.embed_one, centroid=centroid)
    return RetrievalService(
        embedder=emb, translator=None, decomposer=None, router=router,
        general=_searcher(mini_store), code=_searcher(mini_store),
    )


def test_retrieve_general_route(mini_store):
    svc = _service(mini_store)  # keine Centroid → immer general
    r = svc.retrieve("TLS negotiates keys", top_k=4, max_hits=3)
    assert r.route == "general"
    assert r.code is None
    assert r.general is not None
    assert r.general.hits[0].doc_id == "doc-tls"
    assert r.general.hits[0].title == "TLS Handshake"
    assert r.general.verdict is not None
    assert r.general.verdict.best_distance == r.general.best_distance


def test_retrieve_code_route_uses_code_threshold(mini_store):
    query = "Spark is a cluster engine"
    centroid = fake_embedding(query)  # cos(query, centroid) = 1.0 → route "code"
    svc = _service(mini_store, centroid=centroid)
    r = svc.retrieve(query, top_k=4)
    assert r.route == "code"
    assert r.general is None and r.code is not None
    assert r.code.verdict.trust_threshold == 57.0  # Code-Schwelle, nicht 50


def test_retrieve_without_preprocessing_keeps_query(mini_store):
    svc = _service(mini_store)
    r = svc.retrieve("The GIL serializes threads")
    assert r.effective_query == r.query
    assert r.subqueries == [r.query]


def test_retrieve_empty_vault_is_fallback(tmp_path):
    from collect.retrieval.store import VaultStore
    empty_store = VaultStore(tmp_path / "x.monolith", tmp_path / "x.pkl")
    svc = _service(empty_store)
    r = svc.retrieve("irgendwas")
    assert r.general.hits == []
    assert r.general.verdict.zone == ZONE_FALLBACK


def test_multi_subquery_fanout(mini_store):
    class FakeDecomposer:
        def decompose(self, q):
            return {"original": q,
                    "subqueries": ["TLS negotiates keys", "Spark is a cluster engine"],
                    "skipped": False, "cache_hit": False, "duration_ms": 0.0}

    emb = FakeEmbedder()
    svc = RetrievalService(
        embedder=emb, translator=None, decomposer=FakeDecomposer(),
        router=CodeRouter(emb.embed_one, centroid=None),
        general=_searcher(mini_store), code=_searcher(mini_store),
    )
    r = svc.retrieve("Zusammenhang zwischen TLS und Spark", top_k=4, max_hits=4)
    ids = {h.doc_id for h in r.general.hits}
    # Beide Anker geerdet — genau der Zweck der Zerlegung
    assert {"doc-tls", "doc-spark"} <= ids
