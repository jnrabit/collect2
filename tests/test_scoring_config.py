"""Rang 2: Retrieval-Rohwerte aus der Config statt Code-Konstanten.

Das "fließende Einstellen": α/β/γ/δ, RRF-k, Adaptions-Intervall,
Adaptive-Schwellen, Entropie-Mix, Resonanz-Decay, Profil-Overrides —
alles über COLLECT_*-Env steuerbar, Defaults identisch zum alten Verhalten.
"""

from collect.config import settings
from collect.retrieval.chaos import ChaosRetrieval
from collect.retrieval.service import rrf_merge
from tests.conftest import fake_embedding


# ── Basis-Gewichte aus der Config ─────────────────────────────────────────

def test_chaos_weights_from_settings(mini_store, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_alpha", 0.7)
    monkeypatch.setattr(settings, "retrieval_gamma", 0.2)
    chaos = ChaosRetrieval(mini_store, engine=None, field=None,
                           deterministic=True)
    assert (chaos.alpha, chaos.beta, chaos.gamma, chaos.delta) == (0.7, 0.05, 0.2, 0.05)


def test_chaos_mode_weights_from_settings(mini_store, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_chaos_alpha", 0.42)
    chaos = ChaosRetrieval(mini_store, engine=None, field=None,
                           deterministic=False)
    assert chaos.alpha == 0.42


def test_default_weights_unchanged(mini_store):
    # Regressionsschutz: Defaults = exakt die alten Konstanten
    det = ChaosRetrieval(mini_store, engine=None, field=None, deterministic=True)
    assert (det.alpha, det.beta, det.gamma, det.delta) == (0.8, 0.05, 0.1, 0.05)
    nd = ChaosRetrieval(mini_store, engine=None, field=None, deterministic=False)
    assert (nd.alpha, nd.beta, nd.gamma, nd.delta) == (0.5, 0.2, 0.2, 0.1)


# ── RRF-k ─────────────────────────────────────────────────────────────────

def test_rrf_merge_uses_settings_k(monkeypatch):
    a = [("d1", 10.0), ("d2", 20.0)]
    b = [("d2", 15.0), ("d3", 30.0)]
    # k extrem klein → Top-Ränge dominieren massiv; Ergebnis bleibt gültig
    monkeypatch.setattr(settings, "retrieval_rrf_k", 1)
    merged_small = rrf_merge([a, b])
    monkeypatch.setattr(settings, "retrieval_rrf_k", 60)
    merged_default = rrf_merge([a, b])
    # d2 ist in beiden Listen → gewinnt bei jedem k; Distanz = min(20, 15)
    assert merged_small[0][0] == "d2" and merged_default[0][0] == "d2"
    assert dict(merged_default)["d2"] == 15.0
    # explizites k gewinnt über Config
    assert rrf_merge([a, b], k=60) == merged_default


# ── Adaptive-Schwellen ───────────────────────────────────────────────────

def test_adaptive_thresholds_from_settings(mini_store, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_adaptive_explore_until", 2)
    monkeypatch.setattr(settings, "retrieval_adaptive_settle_until", 4)
    chaos = ChaosRetrieval(mini_store, engine=None, field=None,
                           deterministic=True)
    prof = {"adaptive": True}
    v = fake_embedding("TLS negotiates keys")
    chaos.search(v, top_k=2, profile=prof)   # count=1 < 2 → explorativ
    chaos.search(v, top_k=2, profile=prof)   # count=2 → Übergang
    chaos.search(v, top_k=2, profile=prof)   # count=3 → Übergang
    chaos.search(v, top_k=2, profile=prof)   # count=4 → präzise
    assert chaos._adaptive_count == 4


# ── Profil-Overrides (Merge über Presets) ────────────────────────────────

def test_profile_overrides_merge(monkeypatch):
    monkeypatch.setattr(settings, "retrieval_profile_overrides",
                        {"chaos": {"delta": 0.25},
                         "geist": {"alpha": 1.0},          # unbekanntes Profil
                         "broad": {"quatsch": 9.9}})        # unbekannter Key
    profiles = settings.retrieval_profiles
    assert profiles["chaos"]["delta"] == 0.25
    assert profiles["chaos"]["alpha"] == settings.retrieval_chaos_alpha
    assert "geist" not in profiles
    assert "quatsch" not in profiles["broad"]


def test_balanced_preset_follows_base_weights(monkeypatch):
    monkeypatch.setattr(settings, "retrieval_alpha", 0.77)
    assert settings.retrieval_profiles["balanced"]["alpha"] == 0.77
    assert settings.get_profile("balanced")["alpha"] == 0.77


# ── Entropie-Mix & Decay: Default-Formate ────────────────────────────────

def test_entropy_mix_default():
    assert settings.retrieval_entropy_mix == (0.3, 0.1, 0.1, 0.05)


def test_resonance_decay_wiring(tmp_path, monkeypatch):
    from collect.retrieval.resonance import ResonanceField
    monkeypatch.setattr(settings, "resonance_decay", 0.9)
    f = ResonanceField(tmp_path / "field.pkl")
    assert f.decay == 0.9
    # expliziter Wert gewinnt über Config
    g = ResonanceField(tmp_path / "field2.pkl", decay=0.5)
    assert g.decay == 0.5
