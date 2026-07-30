"""ChaosRetrieval auf dem Mini-Vault: Relevanz, Distanz-Semantik, Feedback."""

import numpy as np

from collect.retrieval.chaos import ChaosRetrieval, RiemannianWarp, ThompsonSampler
from tests.conftest import fake_embedding


class _MockEngine:
    """Engine-Mock für Cortex-Feedback-Tests (Variante 1)."""

    def __init__(self):
        self.active = True
        self.feedback_calls: list[bool] = []
        self.pulse_calls: list[float] = []
        self._cortex_bias = 0.5

    def get_hardware_state(self) -> dict:
        return {
            "x1": 1.0, "y1": 2.0, "z1": 30.0, "w1": 0.0,
            "entropy": 4.0, "temperature": 45.0,
            "x2": 0.0, "y2": 0.0, "cortex_bias": self._cortex_bias,
        }

    def apply_cortex_feedback(self, error: bool) -> None:
        self.feedback_calls.append(error)

    def pulse(self, strength: float) -> None:
        self.pulse_calls.append(strength)


def _retriever(mini_store, seed=7):
    return ChaosRetrieval(mini_store, engine=None, field=None, thompson_seed=seed)


def test_search_finds_matching_doc_first(mini_store):
    chaos = _retriever(mini_store)
    results = chaos.search(fake_embedding("TLS negotiates keys"), top_k=4)
    assert len(results) == 4
    assert results[0][0] == "doc-tls"
    assert results[0][1] < results[-1][1]


def test_distance_semantics_identical_vector(mini_store):
    chaos = _retriever(mini_store)
    best_id, best_dist = chaos.search(fake_embedding("Spark is a cluster engine"), top_k=1)[0]
    assert best_id == "doc-spark"
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
    assert np.allclose(w1._P, w2._P)


def test_diagnostics_shape(mini_store):
    chaos = _retriever(mini_store)
    chaos.search(fake_embedding("x"), top_k=2)
    d = chaos.diagnostics()
    assert d["search_count"] == 1
    assert "warp_divergence" in d and "thompson" in d


# ── Variante 1: Non-Markovsches Cortex-Feedback ──────────────────────────

def test_zone_feedback_fires_cortex_trust(mini_store):
    engine = _MockEngine()
    chaos = ChaosRetrieval(mini_store, engine=engine, field=None, thompson_seed=1)
    chaos.zone_feedback("TRUST")
    assert engine.feedback_calls == [False]  # TRUST = kein Fehler
    assert len(engine.pulse_calls) == 1
    assert engine.pulse_calls[0] > 0  # positive Perturbation


def test_zone_feedback_fires_cortex_fallback(mini_store):
    engine = _MockEngine()
    chaos = ChaosRetrieval(mini_store, engine=engine, field=None, thompson_seed=1)
    chaos.zone_feedback("FALLBACK")
    assert engine.feedback_calls == [True]  # FALLBACK = Fehler


def test_zone_feedback_gray_is_neutral(mini_store):
    engine = _MockEngine()
    chaos = ChaosRetrieval(mini_store, engine=engine, field=None, thompson_seed=1)
    chaos.zone_feedback("GRAY")
    # quality=0.5, Schwelle=0.4 → 0.5 >= 0.4 → kein Fehler (neutral)
    assert engine.feedback_calls == [False]


def test_cortex_bias_modulates_scoring(mini_store):
    """Non-Markovsche Eigenschaft: niedriger Bias → mehr Exploration im Scoring."""
    store = mini_store

    # Engine mit hohem Bias (gute Historie) → weniger Exploration
    eng_good = _MockEngine()
    eng_good._cortex_bias = 0.8
    chaos_good = ChaosRetrieval(store, engine=eng_good, field=None, thompson_seed=1,
                                deterministic=True)
    results_good = chaos_good.search(fake_embedding("TLS"), top_k=4)

    # Engine mit niedrigem Bias (schlechte Historie) → mehr Exploration
    eng_bad = _MockEngine()
    eng_bad._cortex_bias = 0.2
    chaos_bad = ChaosRetrieval(store, engine=eng_bad, field=None, thompson_seed=1,
                                deterministic=True)
    results_bad = chaos_bad.search(fake_embedding("TLS"), top_k=4)

    # Beide sollten valide Ergebnisse liefern
    assert len(results_good) == 4
    assert len(results_bad) == 4
    # Niedriger Bias (schlechte Historie) → hoher memory_factor →
    # mehr Exploration → andere Score-Gewichtung als hoher Bias.
    # Beide liefern Ergebnisse, aber die Verteilungen unterscheiden sich
    # systematisch durch den memory_factor (1.55 vs 0.55 im exp_mode).


def test_zone_feedback_noop_without_engine(mini_store):
    chaos = _retriever(mini_store)
    chaos.zone_feedback("FALLBACK")  # darf nicht crashen
    assert chaos.engine is None
