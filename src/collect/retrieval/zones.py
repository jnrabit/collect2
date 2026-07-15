"""Drei-Zonen-Antwortlogik — aus dem stabilisierten Alt-System übernommen.

Distanz-Semantik: ChaosRetrieval liefert dist = (1 - score) * 100.
  - TRUST     (dist <= trust):            volle Antwort
  - GRAY      (trust < dist < soft_max):  Antwort MIT Hinweis "nur entfernte Treffer"
  - FALLBACK  (dist >= soft_max / keine): LLM-Antwort unterdrückt (Halluzinations-Schutz)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from collect.config import settings

# Konvention des Alt-Systems für "keine Treffer" — jetzt konfigurierbar
# (COLLECT_RETRIEVAL_NO_HIT_DISTANCE); Modul-Alias bindet beim Prozess-Start.
NO_HIT_DISTANCE = settings.retrieval_no_hit_distance

ZONE_TRUST = "TRUST"
ZONE_GRAY = "GRAUZONE"
ZONE_FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class ZoneVerdict:
    zone: str                 # TRUST | GRAUZONE | FALLBACK
    best_distance: float
    trust_threshold: float
    soft_max_distance: float

    @property
    def allows_answer(self) -> bool:
        return self.zone != ZONE_FALLBACK

    @property
    def needs_caution_hint(self) -> bool:
        return self.zone == ZONE_GRAY


def fallback_suppressed(is_fallback: bool, grounded: bool) -> bool:
    """Zentrale Unterdrückungs-Entscheidung — vorher in llm.py UND response.py
    dupliziert. FALLBACK ohne Erdung → Antwort unterdrücken (Halluzinations-
    Schutz). COLLECT_FALLBACK_SUPPRESS=false schaltet den Schutz ab; die
    Aufrufer zeigen dann stattdessen einen Nicht-geerdet-Hinweis."""
    return settings.fallback_suppress and is_fallback and not grounded


def classify_zone(best_distance: Optional[float],
                  trust_threshold: Optional[float] = None,
                  soft_max_distance: Optional[float] = None) -> ZoneVerdict:
    trust = settings.vault_trust_threshold if trust_threshold is None else trust_threshold
    soft_max = settings.vault_soft_max_distance if soft_max_distance is None else soft_max_distance
    dist = NO_HIT_DISTANCE if best_distance is None else float(best_distance)

    if dist >= soft_max:
        zone = ZONE_FALLBACK
    elif dist > trust:
        zone = ZONE_GRAY
    else:
        zone = ZONE_TRUST
    return ZoneVerdict(zone=zone, best_distance=dist,
                       trust_threshold=trust, soft_max_distance=soft_max)
