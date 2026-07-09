"""WebSearchAgent — Web-Recherche bei Vault-FALLBACK oder explizitem Trigger.

Zwei Einsatzpunkte:
  1. Explizit: User sagt "recherchiere im web nach X" → direkt suchen
  2. FALLBACK: Vault liefert keine Treffer → automatisch nachsuchen

Phase 7: Page-Fetch — Top-Ergebnisse werden vollständig abgerufen, in Chunks
zerlegt, per MiniLM embedded und die relevantesten Chunks per Cosine-Ähnlichkeit
zur Query ausgewählt (gleiches Pattern wie Ad-hoc-Dateikontext).
"""

from __future__ import annotations

import re
import time
from typing import Optional

import numpy as np

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.config import settings
from collect.search.web import WebSearcher, clean_query_for_search

MAX_SNIPPETS = 6
MAX_SNIPPET_CHARS = 1200
PAGE_FETCH_COUNT = 2
PAGE_CHUNK_CHARS = 1000
PAGE_TOP_CHUNKS = 3


def _chunk_text(text: str, chunk_size: int = PAGE_CHUNK_CHARS) -> list[str]:
    """Teilt Text in ~gleich große Chunks an Satzgrenzen."""
    if len(text) <= chunk_size:
        return [text]
    parts = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for part in parts:
        if len(current) + len(part) > chunk_size and current:
            chunks.append(current)
            current = part
        else:
            current = (current + " " + part).strip()
    if current:
        chunks.append(current)
    return chunks


class WebSearchAgent(BaseAgent):
    name = "websearch"

    def __init__(self, bus, searcher: Optional[WebSearcher] = None,
                 embed_fn=None):
        super().__init__(bus)
        self.searcher = searcher or WebSearcher()
        self.embed_fn = embed_fn
        self.enabled = settings.web_search_enabled
        self._embedder = None

    def _get_embedder(self):
        if self.embed_fn:
            return self.embed_fn
        if self._embedder is None:
            from collect.retrieval.embedding import get_backend
            self._embedder = get_backend()
        return self._embedder.embed_one

    def subscriptions(self):
        return {"web_request": self.on_request}

    def on_request(self, msg: Message) -> None:
        if not self.enabled:
            self.publish("web_response", "web_response",
                         {"hits": [], "skipped": True, "reason": "disabled"},
                         msg.correlation_id)
            return

        query = msg.data.get("query", "")
        explicit = msg.data.get("explicit", False)
        search_query = clean_query_for_search(query) if explicit else query
        if not search_query.strip():
            self.publish("web_response", "web_response",
                         {"hits": [], "skipped": True, "reason": "empty_query"},
                         msg.correlation_id)
            return

        self.log.info("Web-Suche: %s…", search_query[:60])
        results = self.searcher.search(search_query)

        hits = []
        for r in results:
            if not r.get("content"):
                continue
            hits.append({
                "doc_id": f"web:{r['url'][:80]}",
                "title": r["title"],
                "source": r["url"],
                "content": r["content"][:MAX_SNIPPET_CHARS],
            })

        # Page-Fetch: Top-Ergebnisse vollständig abrufen, embedden, relevante
        # Chunks per Cosine auswählen. Ergänzt die Suchergebnisse.
        t0 = time.perf_counter()
        for i, r in enumerate(results[:PAGE_FETCH_COUNT]):
            if not r.get("url"):
                continue
            try:
                page_text = self.searcher.fetch_page(r["url"], max_chars=8000)
                if not page_text or len(page_text) < 200:
                    continue
                chunks = _chunk_text(page_text, PAGE_CHUNK_CHARS)
                if not chunks:
                    continue

                embed_fn = self._get_embedder()
                query_vec = np.asarray(embed_fn(search_query), dtype=np.float32)
                query_vec = query_vec / (np.linalg.norm(query_vec) + 1e-8)

                chunk_vecs = []
                for chunk in chunks:
                    vec = np.asarray(embed_fn(chunk), dtype=np.float32)
                    vec = vec / (np.linalg.norm(vec) + 1e-8)
                    chunk_vecs.append(vec)

                sims = np.stack(chunk_vecs) @ query_vec
                for idx in np.argsort(-sims)[:PAGE_TOP_CHUNKS]:
                    if sims[idx] < 0.3:
                        continue
                    url_short = r["url"].split("/")[-1][:40] or r["url"][:40]
                    hits.append({
                        "doc_id": f"web:{r['url'][:80]}#chunk{idx}",
                        "title": f"{r['title']} [{url_short}]",
                        "source": r["url"],
                        "content": chunks[idx][:MAX_SNIPPET_CHARS],
                    })
            except Exception as e:
                self.log.debug("Page-Fetch %s fehlgeschlagen: %s",
                               r.get("url", "?")[:40], e)

        elapsed = (time.perf_counter() - t0) * 1000
        self.publish("web_response", "web_response", {
            "hits": hits[:MAX_SNIPPETS + PAGE_FETCH_COUNT * PAGE_TOP_CHUNKS],
            "count": len(hits),
            "explicit": explicit,
            "search_query": search_query,
        }, msg.correlation_id)

        self.progress(msg.correlation_id, "web_done",
                      f"{len(hits)} Web-Treffer ({elapsed:.0f}ms)")
        self.log.info("Web-Suche: %d Treffer (%.0fms)", len(hits), elapsed)
