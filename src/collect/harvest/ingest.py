"""VaultIngest — neue Dokumente sicher in den General-Vault sedimentieren.

Vault-Writes sind heikel (259k Docs, 416 MB Cache). Deshalb NIE in-place:
  1. Qualitätsschranke (Länge, Sprache, Dedupe per ID + Content-Hash)
  2. Batch-Embedding (CPU/MiniLM, unnormalisiert — konsistent zum Bestand)
  3. Backup (Archiv + Cache → *.bak-<ts>)
  4. Schreiben nach *.tmp
  5. Verify durch Wiederladen (Doc-Count + Stichproben-Roundtrip + Cache-Keys)
  6. erst dann atomarer os.replace

Rollback: die *.bak-<ts>-Dateien zurückkopieren (Pfade im Ergebnis benannt);
zusätzlich liegt der Ur-Stand unter ~/collect/data (Migrationsquelle).
"""

from __future__ import annotations

import hashlib
import logging
import pickle
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from collect.config import settings
from collect.retrieval.vault import Vault

logger = logging.getLogger(__name__)

MIN_CONTENT_LEN = 200
# Grobe Latein-Skript-Heuristik: ≥60% ASCII-Buchstaben unter den Buchstaben
_LATIN_MIN_RATIO = 0.6


def content_hash(text: str) -> str:
    """Normalisierter Hash (Whitespace-kollabiert) — fängt Near-Dupes."""
    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def looks_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ascii_letters = sum(1 for c in letters if c.isascii())
    return ascii_letters / len(letters) >= _LATIN_MIN_RATIO


@dataclass
class IngestResult:
    added: int = 0
    skipped_dupe: int = 0
    skipped_quality: int = 0
    total_after: int = 0
    backups: list = field(default_factory=list)
    committed: bool = False
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return f"✗ Ingest abgebrochen: {self.error} (Vault unverändert)"
        return (f"✓ {self.added} neu, {self.skipped_dupe} Duplikat(e), "
                f"{self.skipped_quality} Qualität — Vault: {self.total_after} Docs. "
                f"Backups: {', '.join(Path(b).name for b in self.backups)}")


class VaultIngest:
    def __init__(self, embedder=None, vault_file=None, cache_file=None):
        if embedder is None:
            from collect.retrieval.embedding import get_backend
            embedder = get_backend()
        self.embedder = embedder
        self.vault_file = Path(vault_file or settings.knowledge_vault_file)
        self.cache_file = Path(cache_file or settings.knowledge_cache_file)

    def _load(self):
        archive = Vault(self.vault_file).load()
        cache = {}
        if self.cache_file.exists():
            with open(self.cache_file, "rb") as f:
                cache = pickle.load(f)
        return archive, cache

    def _accept(self, doc: dict, known_ids: set, known_hashes: set) -> str:
        """→ '' wenn akzeptiert, sonst Ablehnungsgrund ('dupe'|'quality')."""
        content = str(doc.get("content", ""))
        if len(content) < MIN_CONTENT_LEN or not looks_latin(content):
            return "quality"
        if str(doc.get("id", "")) in known_ids:
            return "dupe"
        if content_hash(content) in known_hashes:
            return "dupe"
        return ""

    def ingest(self, docs, dry_run: bool = False, on_progress=None) -> IngestResult:
        """docs: Iterable von Doc-Dicts (id, content, title, …)."""
        res = IngestResult()
        archive, cache = self._load()
        known_ids = {str(d.get("id")) for d in archive}
        known_hashes = {content_hash(str(d.get("content", ""))) for d in archive}

        fresh = []
        for doc in docs:
            reason = self._accept(doc, known_ids, known_hashes)
            if reason == "dupe":
                res.skipped_dupe += 1
                continue
            if reason == "quality":
                res.skipped_quality += 1
                continue
            fresh.append(doc)
            known_ids.add(str(doc["id"]))
            known_hashes.add(content_hash(str(doc.get("content", ""))))
            if on_progress:
                on_progress(len(fresh), doc.get("title", ""))

        res.added = len(fresh)
        res.total_after = len(archive) + len(fresh)
        if dry_run or not fresh:
            res.committed = False
            return res

        # Embedding (unnormalisiert — Bestandskonsistenz)
        vecs = self.embedder.embed([str(d["content"]) for d in fresh], normalize=False)
        for doc, vec in zip(fresh, vecs):
            archive.append(doc)
            cache[str(doc["id"])] = vec.astype(np.float32)

        try:
            self._safe_write(archive, cache, res)
            res.committed = True
        except Exception as e:
            logger.exception("Vault-Write fehlgeschlagen")
            res.error = str(e)
            res.committed = False
        return res

    def _safe_write(self, archive: list, cache: dict, res: IngestResult) -> None:
        ts = int(time.time())
        # 1. Backup (nur wenn Originale existieren)
        for path in (self.vault_file, self.cache_file):
            if path.exists():
                bak = path.with_suffix(path.suffix + f".bak-{ts}")
                bak.write_bytes(path.read_bytes())
                res.backups.append(str(bak))

        # 2. Schreiben nach tmp
        tmp_vault = self.vault_file.with_suffix(self.vault_file.suffix + ".tmp")
        tmp_cache = self.cache_file.with_suffix(self.cache_file.suffix + ".tmp")
        Vault(tmp_vault).save(archive)
        with open(tmp_cache, "wb") as f:
            pickle.dump(cache, f)

        # 3. Verify durch Wiederladen (bevor irgendetwas Echtes ersetzt wird)
        reloaded = Vault(tmp_vault).load()
        if len(reloaded) != len(archive):
            raise RuntimeError(f"Verify: Doc-Count {len(reloaded)} != {len(archive)}")
        with open(tmp_cache, "rb") as f:
            rcache = pickle.load(f)
        if len(rcache) != len(cache):
            raise RuntimeError(f"Verify: Cache-Count {len(rcache)} != {len(cache)}")
        # Stichprobe: die letzten 3 neuen Docs müssen roundtrippen + Vektor haben
        for doc in archive[-3:]:
            rid = str(doc["id"])
            match = next((d for d in reloaded if str(d.get("id")) == rid), None)
            if match is None or match.get("content") != doc.get("content"):
                raise RuntimeError(f"Verify: Doc {rid} nicht korrekt zurückgelesen")
            if rid not in rcache or rcache[rid].shape != (settings.embedding_dim,):
                raise RuntimeError(f"Verify: Vektor für {rid} fehlt/falsch")

        # 4. Atomarer Move (tmp → echt)
        tmp_vault.replace(self.vault_file)
        tmp_cache.replace(self.cache_file)
        logger.info("Vault committet: %d Docs, %d Vektoren", len(archive), len(cache))
