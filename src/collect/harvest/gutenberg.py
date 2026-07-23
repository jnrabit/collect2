"""Gutenberg Source — klassische Texte via gutendex.com API.

60.000+ gemeinfreie Bücher. Lädt Plain-Text, chunked in 1200-Zeichen-Blöcke.
Liefert Doc-Dicts (id, title, content, source) für den Vault-Ingest.

Port aus AI neu/collectors.py:GutenbergCollector.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)

GUTENDEX = "https://gutendex.com/books"

GUTENBERG_TARGETS = [
    "Poincaré", "Maxwell", "Boltzmann", "Mach",
    "Russell", "Whitehead", "Helmholtz", "Faraday",
    "Darwin", "Newton", "Kepler", "Galileo",
    "Descartes", "Leibniz", "Kant", "Hegel",
    "Hume", "Locke", "Spinoza", "Aristotle",
]

GUTENBERG_SUBJECTS = [
    "science", "physics", "mathematics", "philosophy",
    "natural history", "psychology", "logic",
]


def _search_books(query: str) -> list[dict]:
    url = f"{GUTENDEX}?search={urllib.parse.quote(query)}&languages=en"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read()).get("results", [])
    except Exception as e:
        logger.debug("Gutendex search error (%s): %s", query, str(e)[:80])
        return []


def _fetch_text(book: dict) -> str | None:
    fmts = book.get("formats", {})
    for fmt_name in ["text/plain; charset=utf-8", "text/plain"]:
        url = fmts.get(fmt_name)
        if not url or not url.startswith("http"):
            continue
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                raw = r.read(1_000_000)
            for enc in ["utf-8", "latin-1", "cp1252"]:
                try:
                    return raw.decode(enc)
                except Exception:
                    continue
        except Exception as e:
            logger.debug("Gutenberg fetch error (%s): %s", book.get("id", "?"), str(e)[:80])
            continue
    return None


def _strip_gutenberg(text: str) -> str:
    for marker in ["*** START OF", "***START OF"]:
        idx = text.find(marker)
        if idx >= 0:
            text = text[idx:]
            nl = text.find("\n")
            if nl >= 0:
                text = text[nl:].strip()
    for marker in ["*** END OF", "***END OF"]:
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx].strip()
    return text


def _chunk_text(text: str, size: int = 1200) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 200]
    chunks, cur, cur_len = [], [], 0
    for para in paragraphs[:100]:
        if cur_len + len(para) > size and cur:
            chunks.append(" ".join(cur))
            cur, cur_len = [para], len(para)
        else:
            cur.append(para)
            cur_len += len(para)
    if cur:
        chunks.append(" ".join(cur))
    return chunks[:15]


def harvest_gutenberg(targets: Optional[list[str]] = None,
                      max_books: int = 20) -> list[dict]:
    """Lädt klassische Texte, chunked, gibt Doc-Dicts zurück (id=prefix GB-)."""
    search_terms = (targets or GUTENBERG_TARGETS) + GUTENBERG_SUBJECTS
    loaded_ids: set[str] = set()
    all_docs = []

    for query in search_terms:
        if len(all_docs) >= max_books * 10:
            break
        books = _search_books(query)
        if not books:
            continue

        n_found = 0
        for book in books[:2]:
            bid = str(book.get("id", ""))
            if bid in loaded_ids:
                continue
            loaded_ids.add(bid)

            title = (book.get("title") or "").strip()
            authors = ", ".join(
                a.get("name", "") for a in book.get("authors", [])
            )[:120]

            text = _fetch_text(book)
            if not text or len(text) < 2000:
                continue

            text = _strip_gutenberg(text)
            chunks = _chunk_text(text)
            for i, chunk in enumerate(chunks):
                t = title if i == 0 else f"{title} [{i+1}]"
                doc_id = f"GB-{int(time.time())}-{hash(t) & 0xFFFFFF:06x}"
                all_docs.append({
                    "id": doc_id,
                    "source": "GUTENBERG",
                    "sector": "CLASSIC_TEXT",
                    "title": t,
                    "content": chunk[:1500],
                    "author": authors,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                })

            if chunks:
                n_found += len(chunks)
            time.sleep(1.5)

        if n_found:
            logger.info("GB: '%s' → %d chunks aus %d Büchern", query, n_found,
                        len([b for b in books[:2] if str(b.get("id")) in loaded_ids]))

    return all_docs
