"""Autopilot: Zonen-Erfassung und Streuungs-Messung im DistillationEntry.

Zwei Defekte, die hier abgesichert werden:
  1. Bei route=code wurde für den General-Vault "" protokolliert — dasselbe
     Zeichen wie für "durchsucht, aber kein Urteil". Jetzt NICHT_ABGEFRAGT.
  2. Es gab kein Uneinigkeits-Maß, weshalb der DreamCycle nie Material fand.
"""

import types

import pytest

from collect.retrieval.autopilot import (
    ZONE_NOT_QUERIED,
    DistillationEntry,
    _hit_spread,
    _zone_of,
)
from collect.retrieval.service import VaultHit, VaultResult
from collect.retrieval.zones import ZONE_FALLBACK, ZONE_TRUST, classify_zone


def _result(distances, verdict=None):
    hits = [VaultHit(doc_id=f"D{i}", distance=d) for i, d in enumerate(distances)]
    return VaultResult(hits=hits, verdict=verdict)


# ── Zonen-Erfassung ──────────────────────────────────────────────────────

def test_zone_of_marks_unqueried_vault():
    """route=code → general ist None. Das ist NICHT dasselbe wie FALLBACK:
    FALLBACK behauptete eine Messung, die nie stattgefunden hat."""
    assert _zone_of(None) == ZONE_NOT_QUERIED
    assert _zone_of(None) != ZONE_FALLBACK


def test_zone_of_uses_existing_verdict():
    res = _result([10.0, 12.0], verdict=classify_zone(10.0))
    assert _zone_of(res) == ZONE_TRUST


def test_zone_of_classifies_when_verdict_missing():
    """Durchsucht, aber ohne Urteil → aus best_distance klassifizieren,
    nicht stillschweigend leer lassen."""
    assert _zone_of(_result([10.0, 12.0])) == ZONE_TRUST
    assert _zone_of(_result([90.0, 95.0])) == ZONE_FALLBACK
    # Gar keine Treffer → classify_zone(None) = FALLBACK, das ist gemessen
    assert _zone_of(_result([])) == ZONE_FALLBACK


# ── Streuung ─────────────────────────────────────────────────────────────

def test_hit_spread_is_max_minus_min():
    assert _hit_spread(_result([30.0, 42.5, 35.0])) == pytest.approx(12.5)


def test_hit_spread_needs_at_least_two_hits():
    """Unter 2 Treffern gibt es nichts zu vergleichen. None, nicht 0.0 —
    0.0 hieße 'völlig einig' und würde als Nicht-Widerspruch gezählt."""
    assert _hit_spread(_result([30.0])) is None
    assert _hit_spread(_result([])) is None
    assert _hit_spread(None) is None


def test_hit_spread_zero_when_hits_agree():
    assert _hit_spread(_result([30.0, 30.0])) == 0.0


def test_distillation_entry_serialises_new_fields():
    d = DistillationEntry(query="q", hit_spread=11.25, zone=ZONE_NOT_QUERIED,
                          code_zone=ZONE_TRUST).to_dict()
    assert d["hit_spread"] == 11.25
    assert d["zone"] == ZONE_NOT_QUERIED and d["code_zone"] == ZONE_TRUST
    assert d["timestamp"]                    # wird automatisch gesetzt
