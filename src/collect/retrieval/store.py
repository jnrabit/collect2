"""VaultStore — Dokumente + Embedding-Cache eines Vaults, suchbereit im RAM.

Hält Archiv (Liste von Doc-Dicts), Embedding-Cache (doc_id → 384-dim-Vektor),
die daraus gestapelte float32-Matrix fürs vektorisierte Scoring und einen
id→doc-Index (das Alt-System scannte pro Treffer linear durchs 259k-Archiv).
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Optional

import numpy as np

from collect.retrieval.vault import Vault

logger = logging.getLogger(__name__)


class VaultStore:
    def __init__(self, vault_file: Path, cache_file: Path):
        self.vault_file = Path(vault_file)
        self.cache_file = Path(cache_file)

        self.archive: list[dict] = []
        self.doc_cache: dict = {}
        self.id_map: list = []
        self.matrix: Optional[np.ndarray] = None
        self._doc_index: dict[str, dict] = {}

        self._load_cache()
        self._load_archive()

    def _load_cache(self) -> None:
        if not self.cache_file.exists():
            logger.warning("Embedding-Cache fehlt: %s", self.cache_file)
            return
        try:
            with open(self.cache_file, "rb") as f:
                self.doc_cache = pickle.load(f)
            self.rebuild_matrix()
            logger.info("Cache: %s Vektoren (%s)", f"{len(self.doc_cache):,}", self.cache_file.name)
        except Exception as e:
            logger.error("Cache-Load fehlgeschlagen (%s): %s", self.cache_file, e)

    def _load_archive(self) -> None:
        self.archive = Vault(self.vault_file).load()
        self._doc_index = {str(d.get("id", i)): d for i, d in enumerate(self.archive)}
        logger.info("Vault: %s Dokumente (%s)", f"{len(self.archive):,}", self.vault_file.name)

    def rebuild_matrix(self) -> None:
        if not self.doc_cache:
            self.id_map, self.matrix = [], None
            return
        self.id_map = list(self.doc_cache.keys())
        self.matrix = np.stack(
            [self.doc_cache[i] for i in self.id_map]
        ).astype(np.float32)

    def get_doc(self, doc_id) -> Optional[dict]:
        return self._doc_index.get(str(doc_id))

    def doc_text(self, doc_id) -> str:
        doc = self.get_doc(doc_id)
        if not doc:
            return ""
        # str(): einzelne Vault-Docs tragen ein Dict/Objekt als Feldwert
        return str(doc.get("text", doc.get("content", "")))

    def search_blob(self, doc_id) -> str:
        """Titel + Text als ein String — robuste Basis fürs lexikalische
        Rerank (Felder können in einzelnen Vault-Docs kein str sein)."""
        doc = self.get_doc(doc_id)
        if not doc:
            return ""
        return f"{doc.get('title', '')} {self.doc_text(doc_id)}"

    @property
    def ready(self) -> bool:
        return self.matrix is not None and len(self.archive) > 0

    def stats(self) -> dict:
        return {
            "documents": len(self.archive),
            "vectors": len(self.doc_cache),
            "vault_file": str(self.vault_file),
            "cache_file": str(self.cache_file),
        }
