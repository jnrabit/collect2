"""Ad-hoc-Dateikontext — Pfad-Erkennung, Lesegrenzen, ephemeres Chunk-Retrieval.

Geteilter Baustein für Teil A (Analyse „lies ~/x") und Teil B (Ingest
„lade ~/x"). Kernprinzip: flüchtiges Arbeitsgedächtnis — gelesene Inhalte
sedimentieren NIE in den Vault, kein Cache auf Platte.

Sicherheit: die realpath-Allowlist (`path_allowed`) ist die einzige
Verteidigungslinie — über die (ggf. exponierte) API ist das ein Datei-Lese-
Endpoint. Symlink-/`..`-Escapes müssen scheitern; nur Lesen, nie Schreiben.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from collect.config import settings

logger = logging.getLogger(__name__)

# Pfad-Kandidaten: ~/…, /home/…, absolute /…, ./… oder ../…  (kein bloßes Wort)
_PATH_RE = re.compile(r"(?:~|\.{1,2})?/[\w./\-]+")


def detect_paths(query: str) -> list[str]:
    """Existierende Pfade in der Query (nach Expansion). Kein Pfad → []."""
    out = []
    for m in _PATH_RE.findall(query or ""):
        expanded = os.path.expanduser(m.rstrip(".,;:!?)"))
        if os.path.exists(expanded) and expanded not in out:
            out.append(expanded)
    return out


def path_allowed(path: str | Path, roots: Optional[list[Path]] = None) -> bool:
    """realpath-kanonisiert; True nur wenn UNTERHALB einer Allowlist-Wurzel.

    os.path.realpath löst Symlinks + `..` auf → ein Symlink, der aus der
    Allowlist hinauszeigt, oder ein `../../etc/passwd` scheitert hier.
    """
    roots = roots if roots is not None else settings.effective_read_paths
    real = os.path.realpath(str(path))
    for root in roots:
        root_real = os.path.realpath(str(root))
        if real == root_real or real.startswith(root_real + os.sep):
            return True
    return False


@dataclass
class FileSource:
    path: str
    content: str


def _iter_dir(root: Path, exts: set[str]) -> list[Path]:
    """Text/Code-Dateien unter root, begrenzt nach Tiefe/Anzahl."""
    out: list[Path] = []
    root_depth = len(root.parts)
    for p in sorted(root.rglob("*")):
        if len(out) >= settings.file_max_files:
            break
        if not p.is_file():
            continue
        if len(p.parts) - root_depth > settings.file_max_depth:
            continue
        if any(part in {".git", ".venv", "__pycache__", "node_modules"}
               for part in p.parts):
            continue
        if p.suffix.lower() in exts:
            out.append(p)
    return out


def _read_text_file(p: Path) -> Optional[str]:
    """Text lesen (gecappt); None bei Binär/zu groß/unlesbar."""
    try:
        if p.stat().st_size > settings.file_max_bytes:
            return None
        raw = p.read_bytes()
        if b"\x00" in raw[:4096]:  # Binär-Heuristik
            return None
        return raw.decode("utf-8", errors="replace")
    except OSError as e:
        logger.warning("Datei nicht lesbar (%s): %s", p, e)
        return None


def read_sources(path: str, roots: Optional[list[Path]] = None) -> list[FileSource]:
    """Datei ODER Verzeichnis → FileSources (mit Lesegrenzen). Ohne Freigabe []."""
    if not path_allowed(path, roots):
        return []
    p = Path(path)
    exts = settings.file_ext_set
    total = 0
    out: list[FileSource] = []
    files = [p] if p.is_file() else _iter_dir(p, exts) if p.is_dir() else []
    for f in files:
        if total >= settings.file_max_bytes:
            break
        # Einzeldatei (explizit adressiert) umgeht die Endungs-Allowlist,
        # Verzeichnis-Inhalte nicht.
        text = _read_text_file(f)
        if text is None:
            continue
        out.append(FileSource(path=str(f), content=text))
        total += len(text)
    return out


def _chunk(text: str, size: int) -> list[str]:
    # An Zeilengrenzen bündeln, damit Chunks nicht mitten im Code brechen
    chunks, buf, buf_len = [], [], 0
    for line in text.splitlines(keepends=True):
        if buf_len + len(line) > size and buf:
            chunks.append("".join(buf))
            buf, buf_len = [], 0
        buf.append(line)
        buf_len += len(line)
    if buf:
        chunks.append("".join(buf))
    return chunks or [text]


@dataclass
class FileContext:
    paths: list[str] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)   # {path, text}
    rejected: list[str] = field(default_factory=list)  # außerhalb der Allowlist

    @property
    def has_content(self) -> bool:
        return bool(self.chunks)


def build_file_context(query: str, paths: list[str], embed_fn: Callable,
                       roots: Optional[list[Path]] = None) -> FileContext:
    """Liest die Pfade, wählt die zur Query relevantesten Chunks.

    embed_fn: (list[str]) -> ndarray(n, dim) — der geteilte MiniLM-Embedder.
    Kleine Dateien kommen ganz rein (kein Embedding); große werden gechunkt,
    embedded und per Cosine zur Query auf die Top-k reduziert.
    """
    ctx = FileContext()
    sources: list[FileSource] = []
    for path in paths:
        if not path_allowed(path, roots):
            ctx.rejected.append(path)
            continue
        srcs = read_sources(path, roots)
        sources.extend(srcs)
        ctx.paths.extend(s.path for s in srcs)

    if not sources:
        return ctx

    total_bytes = sum(len(s.content) for s in sources)
    # Klein genug → alles direkt reingeben (Embedding sparen)
    if total_bytes <= settings.file_direct_threshold:
        ctx.chunks = [{"path": s.path, "text": s.content} for s in sources]
        return ctx

    # Sonst: chunken, embedden, Top-k relevanteste zur Query
    candidates: list[dict] = []
    for s in sources:
        for c in _chunk(s.content, settings.file_chunk_chars):
            candidates.append({"path": s.path, "text": c})

    try:
        q_vec = np.asarray(embed_fn([query]), dtype=np.float32)[0]
        q_vec = q_vec / (np.linalg.norm(q_vec) + 1e-8)
        c_vecs = np.asarray(embed_fn([c["text"] for c in candidates]), dtype=np.float32)
        c_norm = c_vecs / (np.linalg.norm(c_vecs, axis=1, keepdims=True) + 1e-8)
        sims = c_norm @ q_vec
        top = np.argsort(-sims)[:settings.file_top_chunks]
        ctx.chunks = [candidates[i] for i in sorted(top)]
    except Exception as e:
        logger.warning("Chunk-Auswahl fehlgeschlagen, nehme erste Chunks: %s", e)
        ctx.chunks = candidates[:settings.file_top_chunks]
    return ctx
