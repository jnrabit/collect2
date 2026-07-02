"""Centroid-Routing pinnen (general/both/code + Inaktiv-Fallback)."""

import numpy as np
import pytest

from collect.retrieval.router import (
    ROUTE_BOTH,
    ROUTE_CODE,
    ROUTE_GENERAL,
    CodeRouter,
    load_or_compute_centroid,
)

DIM = 8


def _unit(i):
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


CENTROID = _unit(0)


def _embed_with_cosine(target_cos):
    """Liefert eine embed_fn, deren Vektor exakt target_cos zur Centroid hat."""
    def fn(query):
        v = CENTROID * target_cos + _unit(1) * np.sqrt(1 - target_cos ** 2)
        return v.astype(np.float32)
    return fn


@pytest.mark.parametrize("cos,expected", [
    (0.50, ROUTE_CODE),      # >= high (0.40)
    (0.40, ROUTE_CODE),
    (0.30, ROUTE_BOTH),      # >= low (0.25)
    (0.25, ROUTE_BOTH),
    (0.10, ROUTE_GENERAL),
])
def test_classify_thresholds(cos, expected):
    router = CodeRouter(_embed_with_cosine(cos), centroid=CENTROID, high=0.40, low=0.25)
    route, score = router.classify("egal")
    assert route == expected
    assert score == pytest.approx(cos, abs=1e-5)


def test_inactive_without_centroid():
    router = CodeRouter(_embed_with_cosine(0.9), centroid=None)
    assert router.classify("egal") == (ROUTE_GENERAL, 0.0)


def test_embed_failure_falls_back_to_general():
    def broken(_q):
        raise RuntimeError("model kaputt")
    router = CodeRouter(broken, centroid=CENTROID)
    assert router.classify("egal") == (ROUTE_GENERAL, 0.0)


def test_centroid_computed_from_cache_and_persisted(tmp_path):
    import pickle
    cache = {f"d{i}": _unit(0) * 2.0 for i in range(4)}  # unnormalisierte Vektoren
    cache_file = tmp_path / "cache.pkl"
    with open(cache_file, "wb") as f:
        pickle.dump(cache, f)
    centroid_file = tmp_path / "centroid.npy"

    c = load_or_compute_centroid(centroid_file, cache_file)
    assert c is not None
    assert np.linalg.norm(c) == pytest.approx(1.0, abs=1e-5)  # normalisiert
    assert centroid_file.exists()  # persistiert für den nächsten Start

    # Zweiter Aufruf lädt die persistierte Datei
    c2 = load_or_compute_centroid(centroid_file, tmp_path / "gibtsnicht.pkl")
    assert np.allclose(c, c2)


def test_missing_everything_returns_none(tmp_path):
    assert load_or_compute_centroid(tmp_path / "a.npy", tmp_path / "b.pkl") is None
