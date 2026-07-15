"""RetrievalAgent — ein Vault, eine Instanz (general + code laufen parallel).

Sucht per VaultSearcher (Phase 2) über alle Teilfragen, klassifiziert das
Ergebnis in die Drei-Zonen und publiziert einen kompakten Beitrag: Hits mit
gekürztem Content (Bus-Nachrichten klein halten) + Zonen-Verdikt.
"""

from __future__ import annotations

from typing import Optional

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.config import settings
from collect.retrieval.service import VaultSearcher
from collect.retrieval.zones import classify_zone

# Konfigurierbar (COLLECT_RETRIEVAL_MAX_HITS / _MAX_CONTENT_CHARS);
# Modul-Aliase binden beim Prozess-Start, Laufzeit nutzt settings.
# ACHTUNG: max_content_chars MUSS ≥ llm_doc_chars sein — der Bus kürzt hier
# ZUERST, ein kleinerer Wert kastriert llm_doc_chars still.
MAX_HITS = settings.retrieval_max_hits
MAX_CONTENT = settings.retrieval_max_content_chars


class RetrievalAgent(BaseAgent):
    def __init__(self, bus, searcher: VaultSearcher, embedder,
                 kind: str = "retrieval", trust_threshold: Optional[float] = None):
        self.name = kind  # "retrieval" (General) oder "code_retrieval"
        super().__init__(bus)
        self.searcher = searcher
        self.embedder = embedder
        self.kind = kind
        self.trust_threshold = trust_threshold

    def subscriptions(self):
        return {f"{self.kind}_request": self.on_request}

    def on_request(self, msg: Message) -> None:
        query = msg.data.get("query", "")
        subqueries = msg.data.get("subqueries") or [query]
        cid = msg.correlation_id

        hits, best = [], None
        if self.searcher.store.ready:
            profile = msg.data.get("profile")
            vecs = list(self.embedder.embed(subqueries))
            merged = self.searcher.search(vecs, top_k=30, query_text=query,
                                          profile=profile)
            hits = self.searcher.hits(merged, settings.retrieval_max_hits)
            best = self.searcher.best_distance(merged)

        verdict = classify_zone(best, trust_threshold=self.trust_threshold)

        self.publish(f"{self.kind}_response", f"{self.kind}_response", {
            "hits": [{
                "doc_id": h.doc_id,
                "distance": round(h.distance, 2),
                "title": h.title,
                "source": h.source,
                "content": h.content[:settings.retrieval_max_content_chars],
            } for h in hits],
            "zone": verdict.zone,
            "best_distance": verdict.best_distance,
            "count": len(hits),
        }, cid)
        self.progress(cid, f"{self.kind}_done",
                      f"{len(hits)} Treffer, Zone {verdict.zone}")
        self.log.info("%s: %d Hits, Zone %s (best=%.1f)",
                      cid[:8], len(hits), verdict.zone, verdict.best_distance)
