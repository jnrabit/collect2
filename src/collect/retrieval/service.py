"""RetrievalService — der eine Einstiegspunkt für Vault-Suche.

Fluss pro Query:
  1. Translate (DE→EN, Heuristik-gated)
  2. Route (Centroid: general / both / code)
  3. Decompose (Mehr-Aspekt → Teilfragen, Heuristik-gated)
  4. Pro Teilfrage: embed → ChaosRetrieval pro Vault
  5. RRF-Fusion über Teilfragen (Distanz = min über Teilfragen)
  6. Drei-Zonen-Verdikt pro Vault (General- und Code-Schwellen getrennt)

Phase 3 hängt die Redis-Agenten vor diesen Service; die Logik hier bleibt
transportfrei und ist ohne Redis testbar (DESIGN.md §5.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from collect.config import settings
from collect.retrieval.chaos import ChaosRetrieval
from collect.retrieval.native import NativeEngine
from collect.retrieval.resonance import ResonanceField
from collect.retrieval.router import ROUTE_BOTH, ROUTE_CODE, ROUTE_GENERAL, CodeRouter
from collect.retrieval.store import VaultStore
from collect.retrieval.zones import ZoneVerdict, classify_zone

logger = logging.getLogger(__name__)

RRF_K = 60  # Standard-Konstante für Reciprocal Rank Fusion


@dataclass
class VaultHit:
    doc_id: str
    distance: float
    title: str = ""
    source: str = ""
    content: str = ""


@dataclass
class VaultResult:
    hits: list[VaultHit] = field(default_factory=list)
    verdict: Optional[ZoneVerdict] = None

    @property
    def best_distance(self) -> Optional[float]:
        return min((h.distance for h in self.hits), default=None)


@dataclass
class RetrievalResult:
    query: str
    effective_query: str          # nach Translation
    subqueries: list[str]
    route: str                    # general | both | code
    route_score: float
    general: Optional[VaultResult] = None
    code: Optional[VaultResult] = None
    diagnostics: dict = field(default_factory=dict)


def rrf_merge(ranked_lists: list[list[tuple]], k: int = RRF_K) -> list[tuple]:
    """Fusioniert mehrere (doc_id, distance)-Rankings per Reciprocal Rank Fusion.

    Reihenfolge nach RRF-Score; die Distanz eines Docs ist das MINIMUM über
    alle Listen (beste Nähe zählt — darauf sind die Zonen-Schwellen bezogen).
    """
    if len(ranked_lists) == 1:
        return ranked_lists[0]
    scores: dict = {}
    best_dist: dict = {}
    for ranking in ranked_lists:
        for rank, (doc_id, dist) in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            if doc_id not in best_dist or dist < best_dist[doc_id]:
                best_dist[doc_id] = dist
    ordered = sorted(scores, key=scores.get, reverse=True)
    return [(doc_id, best_dist[doc_id]) for doc_id in ordered]


class VaultSearcher:
    """Ein Vault: Store + eigene Engine-Instanz + Resonanzfeld + ChaosRetrieval."""

    def __init__(self, vault_file, cache_file, field_file,
                 thompson_seed: Optional[int] = None):
        self.store = VaultStore(vault_file, cache_file)
        self.engine = NativeEngine()
        self.field = ResonanceField(field_file)
        if self.store.doc_cache:
            self.field.register_documents(self.store.doc_cache)
        self.chaos = ChaosRetrieval(self.store, self.engine, self.field,
                                    embed_dim=settings.embedding_dim,
                                    thompson_seed=thompson_seed)

    def search(self, query_vecs: list, top_k: int) -> list[tuple]:
        """Sucht pro Query-Vektor, fusioniert per RRF → [(doc_id, dist), …]."""
        if not self.store.ready:
            return []
        rankings = [self.chaos.search(v, top_k=top_k) for v in query_vecs]
        rankings = [r for r in rankings if r]
        if not rankings:
            return []
        return rrf_merge(rankings)[:top_k]

    def hits(self, merged: list[tuple], max_hits: int) -> list[VaultHit]:
        out = []
        for doc_id, dist in merged[:max_hits]:
            doc = self.store.get_doc(doc_id)
            if not doc:
                continue
            out.append(VaultHit(
                doc_id=str(doc_id), distance=float(dist),
                title=doc.get("title", ""), source=doc.get("source", ""),
                content=doc.get("text", doc.get("content", "")),
            ))
        return out


_AUTO = object()  # Sentinel: "aus Config bauen" — None heißt "explizit aus"


class RetrievalService:
    def __init__(self, embedder=None, translator=_AUTO, decomposer=_AUTO,
                 router: Optional[CodeRouter] = None,
                 general: Optional[VaultSearcher] = None,
                 code: Optional[VaultSearcher] = None):
        """Alle Abhängigkeiten injizierbar (Tests). translator/decomposer:
        _AUTO → aus Config (falls enabled), None → explizit deaktiviert."""
        if embedder is None:
            from collect.retrieval.embedding import get_backend
            embedder = get_backend()
        self.embedder = embedder

        if translator is _AUTO:
            translator = None
            if settings.translate_enabled:
                from collect.retrieval.translator import QueryTranslator
                translator = QueryTranslator()
        self.translator = translator

        if decomposer is _AUTO:
            decomposer = None
            if settings.decompose_enabled:
                from collect.retrieval.decomposer import QueryDecomposer
                decomposer = QueryDecomposer()
        self.decomposer = decomposer

        self.router = router if router is not None else CodeRouter.from_config(
            lambda q: self.embedder.embed_one(q))

        self.general = general if general is not None else VaultSearcher(
            settings.knowledge_vault_file, settings.knowledge_cache_file,
            settings.knowledge_field_file)
        self.code = code if code is not None else VaultSearcher(
            settings.code_vault_file, settings.code_cache_file,
            settings.code_field_file)

    # ── Hauptpfad ────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 30, max_hits: int = 10) -> RetrievalResult:
        # 1. Translate
        effective = query
        translation = None
        if self.translator:
            translation = self.translator.translate(query)
            effective = translation["translated"]

        # 2. Route
        route, route_score = self.router.classify(effective)

        # 3. Decompose
        subqueries = [effective]
        decomposition = None
        if self.decomposer:
            decomposition = self.decomposer.decompose(effective)
            subqueries = decomposition["subqueries"]

        # 4. Embed (ein Batch für alle Teilfragen)
        vecs = list(self.embedder.embed(subqueries))

        result = RetrievalResult(
            query=query, effective_query=effective, subqueries=subqueries,
            route=route, route_score=route_score,
            diagnostics={
                "translation": translation,
                "decomposition": decomposition,
            },
        )

        # 5+6. Suche + Zonen pro Vault
        if route in (ROUTE_GENERAL, ROUTE_BOTH):
            merged = self.general.search(vecs, top_k)
            result.general = VaultResult(hits=self.general.hits(merged, max_hits))
            result.general.verdict = classify_zone(result.general.best_distance)

        if route in (ROUTE_CODE, ROUTE_BOTH):
            merged = self.code.search(vecs, top_k)
            result.code = VaultResult(hits=self.code.hits(merged, max_hits))
            result.code.verdict = classify_zone(
                result.code.best_distance,
                trust_threshold=settings.code_vault_trust_threshold)

        logger.info(
            "Retrieval: route=%s (%.3f) | general=%s | code=%s",
            route, route_score,
            f"{len(result.general.hits)} Hits/{result.general.verdict.zone}" if result.general else "—",
            f"{len(result.code.hits)} Hits/{result.code.verdict.zone}" if result.code else "—",
        )
        return result
