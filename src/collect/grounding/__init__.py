"""Grounding & Lernen (Phase 4): Ossifikat-Triple-Store + Triplet-Logging.

Grounding-Schleife (aus vibelike): Antworten erzeugen Staging-Tripel →
menschliche Bestätigung (ossifikat-CLI) macht sie VERBÜRGT → verbürgte Fakten
erden künftige Antworten mit Vorrang vor Vault-Quellen.
"""

from collect.grounding.facts import FactGrounder
from collect.grounding.triplets import log_triplet, stable_hash

__all__ = ["FactGrounder", "log_triplet", "stable_hash"]
