"""Retrieval-Kern: Dual-Vault-Suche (Quelibrium/ChaosRetrieval) mit
Query-Vorverarbeitung, Centroid-Routing und Drei-Zonen-Antwortlogik.

Einstiegspunkt: `RetrievalService.retrieve(query)`.
"""

from collect.retrieval.service import (
    RetrievalResult,
    RetrievalService,
    VaultHit,
    VaultResult,
    VaultSearcher,
    rrf_merge,
)
from collect.retrieval.zones import ZoneVerdict, classify_zone

__all__ = [
    "RetrievalService", "RetrievalResult", "VaultResult", "VaultHit",
    "VaultSearcher", "rrf_merge", "ZoneVerdict", "classify_zone",
]
