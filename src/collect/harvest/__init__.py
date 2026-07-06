"""Harvest — kontrolliertes Auftauen des General-Vaults (Phase C).

Ein Quell-Adapter (Wikipedia) + Ingest-Pfad mit Qualitätsschranke, Dedupe,
CPU-Embedding und sicherem Vault-Write (Backup → tmp → Reload-Verify →
atomarer Move). Einstieg: `collect-harvest`. Kein Daemon, kein Scheduler —
manuelle, idempotent wiederholbare Läufe.
"""

from collect.harvest.ingest import IngestResult, VaultIngest
from collect.harvest.wikipedia import WikipediaSource

__all__ = ["VaultIngest", "IngestResult", "WikipediaSource"]
