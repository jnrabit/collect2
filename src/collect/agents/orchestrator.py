"""OrchestratorAgent — Eingangs-Klassifikation + Contribution-Manifest.

Empfängt user_query, macht die billige Vorverarbeitung (Translate → Route →
Decompose → Plan-Erkennung) und sagt dem ResponseAgent EXPLIZIT, welche
Beiträge kommen werden (Manifest, DESIGN.md §5.5) — finalisiert wird bei
Vollständigkeit oder Deadline, nie per Ratelogik.

Pfade:
  - Plan-Query   → manifest [planning],   planning_request (exklusiv)
  - general      → manifest [retrieval, llm]
  - both / code  → manifest [+ code_retrieval]
"""

from __future__ import annotations

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.config import settings
from collect.retrieval.router import ROUTE_GENERAL

import re

# Ersetzt die Substring-Heuristik des Alt-Systems. Deren Fehlerklassen:
# 'plan' in 'explain' → JEDE englische Explain-Frage startete die Kaskade;
# 'phase' triggerte auf "Phase-Locking", 'aufgabe'/'schritt'/'projekt' auf
# gewöhnliche Wissensfragen. Jetzt: Wortgrenzen + Plan-SUBSTANTIVE bzw.
# Imperativ ("erstelle/plane/organisiere …") in Kombination mit Plan-Objekt.
_PLAN_NOUNS = re.compile(
    r"\b(plan|pläne|plaene|ablaufplan(s|es)?|aufgabenplan(s|es)?|roadmap|workflow|"
    r"schritte|steps)\b",  # Plural! Singular ('Schritt für Schritt') ist Erklär-Sprache
    re.IGNORECASE)
_PLAN_IMPERATIVE = re.compile(
    r"\b(erstelle?|plane?|organisiere?|koordiniere?|strukturiere?|"
    r"create|organize|coordinate)\b",
    re.IGNORECASE)


def is_plan_query(query: str) -> bool:
    """Plan-Substantiv reicht; ein Imperativ-Verb nur zusammen mit einem
    Handlungs-Objekt im Satz (verhindert 'Erkläre mir …'-Fehltreffer)."""
    if _PLAN_NOUNS.search(query):
        return True
    if _PLAN_IMPERATIVE.search(query):
        return bool(re.search(
            r"\b(schritte|steps|verzeichnis|datei(en)?|directory|file|struktur)\b",
            query, re.IGNORECASE))
    return False


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"

    def __init__(self, bus, router, translator=None, decomposer=None):
        super().__init__(bus)
        self.router = router
        self.translator = translator
        self.decomposer = decomposer

    def subscriptions(self):
        return {"user_query": self.on_user_query}

    def on_user_query(self, msg: Message) -> None:
        query = (msg.data.get("query") or "").strip()
        cid = msg.correlation_id
        if not query:
            return
        self.log.info("Anfrage %s: %s…", cid[:8], query[:60])
        self.progress(cid, "query_received", query[:80])

        # Plan-Queries laufen exklusiv über die Kaskade
        if is_plan_query(query):
            self._dispatch_plan(query, cid, msg)
            return

        # 1. Translate (Heuristik-gated, best-effort)
        effective = query
        if self.translator:
            try:
                effective = self.translator.translate(query)["translated"]
            except Exception as e:
                self.log.warning("Translate fehlgeschlagen: %s", e)
        if effective != query:
            self.progress(cid, "translated", effective[:80])

        # 2. Route (Centroid)
        route, score = self.router.classify(effective)
        self.progress(cid, "routing", f"{route} (cosine={score:.3f})")

        # 3. Decompose (Heuristik-gated, best-effort)
        subqueries = [effective]
        if self.decomposer:
            try:
                subqueries = self.decomposer.decompose(effective)["subqueries"]
            except Exception as e:
                self.log.warning("Decompose fehlgeschlagen: %s", e)

        # 4. Manifest: was der ResponseAgent erwarten darf
        expected = ["retrieval", "llm"]
        if route != ROUTE_GENERAL:
            expected.append("code_retrieval")
        self.publish("response_manifest", "response_manifest", {
            "query": query,
            "expected": expected,
            "deadline": settings.response_deadline,
            "route": route,
        }, cid, reply_to=msg.reply_to)

        # 5. Requests. llm_request ZUERST (der LLMAgent puffert zwar frühe
        # Retrieval-Beiträge, aber so entsteht das Race gar nicht erst),
        # dann die Retrieval-Requests parallel.
        request = {"query": effective, "subqueries": subqueries, "route": route}
        self.publish("llm_request", "llm_request", {
            **request,
            "original_query": query,
            "needs": [e for e in expected if e != "llm"],
        }, cid)
        self.publish("retrieval_request", "retrieval_request", request, cid)
        if route != ROUTE_GENERAL:
            self.publish("code_retrieval_request", "code_retrieval_request", request, cid)

    def _dispatch_plan(self, query: str, cid: str, msg: Message) -> None:
        self.log.info("Planungs-Anfrage erkannt — Plan→Decide→Act-Kaskade.")
        self.progress(cid, "planning_started", "Kaskade wird initiiert")
        self.publish("response_manifest", "response_manifest", {
            "query": query,
            "expected": ["planning"],
            "deadline": settings.plan_deadline,
        }, cid, reply_to=msg.reply_to)
        self.publish("planning_request", "planning_request", {
            "action": "create_and_execute",
            "query": query,
        }, cid)
