"""Drei-Zonen-Logik pinnen (Schwellwerte + Grenzverhalten aus dem Alt-System)."""

from collect.retrieval.zones import (
    NO_HIT_DISTANCE,
    ZONE_FALLBACK,
    ZONE_GRAY,
    ZONE_TRUST,
    classify_zone,
)


def test_trust_zone():
    v = classify_zone(42.0, trust_threshold=55.0, soft_max_distance=68.0)
    assert v.zone == ZONE_TRUST
    assert v.allows_answer and not v.needs_caution_hint


def test_gray_zone():
    v = classify_zone(57.3, trust_threshold=55.0, soft_max_distance=68.0)
    assert v.zone == ZONE_GRAY
    assert v.allows_answer and v.needs_caution_hint


def test_fallback_zone():
    v = classify_zone(70.0, trust_threshold=55.0, soft_max_distance=68.0)
    assert v.zone == ZONE_FALLBACK
    assert not v.allows_answer


def test_boundaries_match_legacy_semantics():
    # Alt-System: Hard Fallback bei dist >= SOFT_MAX; Grauzone bei dist > TRUST
    assert classify_zone(68.0, 55.0, 68.0).zone == ZONE_FALLBACK
    assert classify_zone(55.0, 55.0, 68.0).zone == ZONE_TRUST
    assert classify_zone(55.01, 55.0, 68.0).zone == ZONE_GRAY


def test_no_hits_is_fallback():
    v = classify_zone(None, 55.0, 68.0)
    assert v.zone == ZONE_FALLBACK
    assert v.best_distance == NO_HIT_DISTANCE


def test_defaults_from_settings():
    v = classify_zone(10.0)
    # Kalibrierung fürs Chaos-Scoring (siehe config + Benchmark)
    assert v.trust_threshold == 50.0
    assert v.soft_max_distance == 62.0
