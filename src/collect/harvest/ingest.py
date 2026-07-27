"""VaultIngest — neue Dokumente sicher in den General-Vault sedimentieren.

Vault-Writes sind heikel (259k Docs, 416 MB Cache). Deshalb NIE in-place:
  1. Qualitätsschranke (Länge, Sprache, Dedupe per ID + Content-Hash)
  2. Batch-Embedding (CPU/MiniLM, unnormalisiert — konsistent zum Bestand)
  3. Backup (Archiv + Cache → *.bak-<ts>)
  4. Schreiben nach *.tmp
  5. Verify durch Wiederladen (Doc-Count + Stichproben-Roundtrip + Cache-Keys)
  6. erst dann atomarer os.replace
  7. Backup-Rotation: älteste *.bak-<ts> löschen (settings.ingest_keep_backups)

Rollback: die *.bak-<ts>-Dateien zurückkopieren (Pfade im Ergebnis benannt);
zusätzlich liegt der Ur-Stand unter ~/collect/data (Migrationsquelle).

Die Rotation läuft bewusst NACH dem Commit: schlägt der Write fehl, bleiben
alle bisherigen Rollback-Punkte erhalten. Sie kann den Ingest auch nicht mehr
scheitern lassen — zu dem Zeitpunkt ist der Vault bereits sicher geschrieben.
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
    pruned_backups: int = 0
    committed: bool = False
    error: str = ""
    by_source_new: dict = field(default_factory=dict)
    by_source_dupe: dict = field(default_factory=dict)

    def summary(self, verbose: bool = True) -> str:
        if self.error:
            return f"✗ Ingest abgebrochen: {self.error} (Vault unverändert)"
        base = (f"✓ {self.added} neu, {self.skipped_dupe} Duplikat(e), "
                f"{self.skipped_quality} Qualität — Vault: {self.total_after} Docs")
        if verbose:
            sources = self.by_source_new.copy()
            for src, n in self.by_source_dupe.items():
                sources[src] = sources.get(src, 0)  # behält 0 für nur-dupe
            if sources:
                parts = []
                for src in sorted(sources, key=lambda s: -(self.by_source_new.get(s, 0) + self.by_source_dupe.get(s, 0))):
                    n = self.by_source_new.get(src, 0)
                    d = self.by_source_dupe.get(src, 0)
                    tag = _source_tag(src)
                    if n:
                        parts.append(f"{tag}+{n}" + (f"/{d}dupe" if d else ""))
                    elif d:
                        parts.append(f"{tag}{d}dupe")
                base += " | " + " ".join(parts)
            base += f"\n  Backups: {', '.join(Path(b).name for b in self.backups)}"
            if self.pruned_backups:
                base += f" (+{self.pruned_backups} alte rotiert)"
        return base


def _source_tag(source: str) -> str:
    """Komprimiertes Source-Tag für die Summary-Zeile."""
    s = source.lower()
    if "arxiv" in s: return "📄AX"
    if "wiki" in s or "crawler" in s: return "📚WK"
    if "semantic" in s: return "🔬S2"
    if "openalex" in s: return "📊OA"
    if "gutenberg" in s: return "📜GB"
    if "stackexchange" in s: return "💬SE"
    if "rfc" in s: return "📋RF"
    return s[:8]


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
            src = str(doc.get("source", "unknown"))
            reason = self._accept(doc, known_ids, known_hashes)
            if reason == "dupe":
                res.skipped_dupe += 1
                res.by_source_dupe[src] = res.by_source_dupe.get(src, 0) + 1
                continue
            if reason == "quality":
                res.skipped_quality += 1
                continue
            fresh.append(doc)
            known_ids.add(str(doc["id"]))
            known_hashes.add(content_hash(str(doc.get("content", ""))))
            res.by_source_new[src] = res.by_source_new.get(src, 0) + 1
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

        # 5. Rotation — erst jetzt, der Commit ist durch. Darf nie werfen.
        keep = settings.ingest_keep_backups
        for path in (self.vault_file, self.cache_file):
            try:
                res.pruned_backups += _rotate_backups(path, keep)
            except Exception:
                logger.warning("Backup-Rotation für %s fehlgeschlagen "
                               "(Vault ist committet)", path.name, exc_info=True)


def _rotate_backups(path: Path, keep: int) -> int:
    """Löscht alle bis auf die `keep` jüngsten *.bak-<ts> neben `path`.

    Sortiert nach dem Zeitstempel IM NAMEN, nicht nach mtime: ein Kopieren
    oder Restore verschiebt die mtime, der Name bleibt die Wahrheit darüber,
    welchen Vault-Stand ein Backup enthält.
    """
    if keep < 1:
        raise ValueError(f"keep muss >= 1 sein, war {keep}")

    dated: list[tuple[int, Path]] = []
    for bak in path.parent.glob(path.name + ".bak-*"):
        suffix = bak.name.rsplit(".bak-", 1)[-1]
        if not suffix.isdigit():
            continue  # fremde Datei — nicht anfassen
        dated.append((int(suffix), bak))

    pruned = 0
    for _, bak in sorted(dated, reverse=True)[keep:]:
        bak.unlink(missing_ok=True)
        logger.info("Backup rotiert: %s", bak.name)
        pruned += 1
    return pruned
