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
from collect.retrieval.profiles import parse_profile_override, resolve_profile
from collect.retrieval.router import ROUTE_GENERAL
from collect.search.web import is_web_request

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


# Code-Workflow: explizites `code:`-Prefix ODER Implementier-Verb +
# Code-Objekt. Bewusst konservativ — Wissensfragen über Code ("Wie
# funktioniert eine Klasse?") gehen weiter den Retrieval-Weg.
_CODE_VERBS = re.compile(
    r"\b(implementiere?|implement|refaktoriere?|refactor|fixe?|bugfix|"
    r"schreibe?|write|baue?|build)\b", re.IGNORECASE)
_CODE_OBJECTS = re.compile(
    r"\b(funktion(en)?|function(s)?|klasse(n)?|class(es)?|methode(n)?|"
    r"method(s)?|modul(e)?|module(s)?|test(s)?|skript(e)?|script(s)?|"
    r"bug(s)?|code)\b", re.IGNORECASE)


def is_code_task(query: str) -> bool:
    if query.strip().lower().startswith("code:"):
        return True
    return bool(_CODE_VERBS.search(query) and _CODE_OBJECTS.search(query))


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"

    def __init__(self, bus, router, translator=None, decomposer=None,
                 rewrite_fn=None):
        super().__init__(bus)
        self.router = router
        self.translator = translator
        self.decomposer = decomposer
        if rewrite_fn is None:
            from collect.retrieval.rewriter import rewrite as rewrite_fn
        self.rewrite_fn = rewrite_fn

    def subscriptions(self):
        return {"user_query": self.on_user_query}

    def on_user_query(self, msg: Message) -> None:
        query = (msg.data.get("query") or "").strip()
        # Profil-Prefix ([precise] Frage?) SOFORT abtrennen — er ist eine
        # UI-Direktive, kein Inhalt, und darf nicht in Rewrite/Übersetzung/
        # Routing/Embedding/LLM-Prompt landen.
        query, profile_override = parse_profile_override(query)
        cid = msg.correlation_id
        if not query:
            return
        self.log.info("Anfrage %s: %s…", cid[:8], query[:60])
        self.progress(cid, "query_received", query[:80])

        # Session-Kontext laden (persistente Sessions, Prio 2)
        session_id = msg.data.get("session_id")
        if session_id:
            try:
                from collect.session import SessionStore
                store = SessionStore()
                msg.data["history"] = store.build_context(session_id)
            except Exception as e:
                self.log.warning("Session-Load fehlgeschlagen: %s", e)

        # Code-Task VOR Plan prüfen (Implementier-Tasks enthalten oft
        # Plan-Vokabular); beide Pfade laufen exklusiv und sind einzeln
        # abschaltbar (COLLECT_WORKFLOW_ENABLED / COLLECT_PLANNING_ENABLED —
        # aus = die Query läuft als normale Wissensfrage weiter).
        if settings.workflow_enabled and is_code_task(query):
            self._dispatch_workflow(query, cid, msg)
            return
        if settings.planning_enabled and is_plan_query(query):
            self._dispatch_plan(query, cid, msg)
            return

        # 0. Explizite Web-Recherche? "recherchiere im web nach X"
        # Prio 3: User kann proaktiv Web-Suche anfordern.
        explicit_web = False
        if settings.web_search_enabled and is_web_request(query):
            explicit_web = True
            self.progress(cid, "web_triggered", "Web-Recherche angefordert")

        # 0. Ad-hoc-Dateikontext: zeigt die Query auf existierende Pfade?
        # Deterministisches Gate, kein LLM. Freigegeben → Datei-Beitrag ergänzt
        # das normale Retrieval. Nicht freigegeben → klare Ablehnung, KEIN
        # stiller Fallback auf Vault-Suche.
        file_paths, file_rejected = [], []
        if settings.file_context_enabled:
            from collect.retrieval.filecontext import detect_paths, path_allowed
            for p in detect_paths(query):
                (file_paths if path_allowed(p) else file_rejected).append(p)
            if file_rejected and not file_paths:
                self._reject_paths(file_rejected, cid, msg)
                return

        # 0. Follow-up-Rewrite (Gate-Heuristik + Historie, best-effort).
        # Das Original bleibt als Fusion-Subquery erhalten (siehe unten).
        history = msg.data.get("history") or []
        rewritten_query = None
        effective = query
        if history:
            try:
                rw = self.rewrite_fn(query, history)
                if rw["applied"]:
                    effective = rw["rewritten"]
                    rewritten_query = rw["rewritten"]
                    self.progress(cid, "rewritten", effective[:80])
            except Exception as e:
                self.log.warning("Rewrite fehlgeschlagen: %s", e)

        # 1. Translate (Heuristik-gated, best-effort)
        pre_translate = effective
        if self.translator:
            try:
                effective = self.translator.translate(effective)["translated"]
            except Exception as e:
                self.log.warning("Translate fehlgeschlagen: %s", e)
        if effective != pre_translate:
            self.progress(cid, "translated", effective[:80])

        # 2. Route (Centroid)
        route, score = self.router.classify(effective)
        self.progress(cid, "routing", f"{route} (cosine={score:.3f})")

        # 2b. Retrieval-Profil: Prefix-Override > konfiguriert > Auto-Detect.
        # Auto-Detect läuft auf der Original-Query (deutsche Signale), nicht
        # auf der übersetzten effective-Query.
        profile_name = profile_override or resolve_profile(
            query, settings.retrieval_profile)
        profile = settings.get_profile(profile_name)
        if profile_override or profile_name != "balanced":
            self.progress(cid, "profile", profile_name)

        # 3. Decompose (Heuristik-gated, best-effort)
        subqueries = [effective]
        if self.decomposer:
            try:
                subqueries = self.decomposer.decompose(effective)["subqueries"]
            except Exception as e:
                self.log.warning("Decompose fehlgeschlagen: %s", e)

        # Fusion-Sicherung: nach einem Rewrite läuft das ORIGINAL als
        # zusätzliche Subquery mit — ein schlechtes Rewrite kann das
        # Ergebnis so nie unter den Status quo drücken (RRF fusioniert).
        if rewritten_query and query not in subqueries:
            subqueries = subqueries + [query]

        # 4. Manifest: was der ResponseAgent erwarten darf
        expected = ["retrieval", "llm"]
        if route != ROUTE_GENERAL:
            expected.append("code_retrieval")
        if file_paths:
            expected.append("file")
        self.publish("response_manifest", "response_manifest", {
            "query": query,
            "rewritten_query": rewritten_query,
            "expected": expected,
            "deadline": settings.response_deadline,
            "route": route,
        }, cid, reply_to=msg.reply_to)

        # 5. Requests. llm_request ZUERST (der LLMAgent puffert zwar frühe
        # Retrieval-Beiträge, aber so entsteht das Race gar nicht erst),
        # dann die Retrieval-Requests parallel.
        request = {"query": effective, "subqueries": subqueries, "route": route,
                   "profile": profile}
        # referential: bezieht sich die Frage auf den Vorkontext (Rückbezug)
        # oder ist sie ein eigenständiger Themenwechsel? Steuert, wie stark der
        # LLM die Historie nutzt (verhindert Themen-Kontamination).
        from collect.retrieval.rewriter import is_referential
        referential = bool(history) and is_referential(query)
        self.publish("llm_request", "llm_request", {
            **request,
            "original_query": query,
            "needs": [e for e in expected if e != "llm"],
            # Gesprächskontext: nur für die Synthese — Retrieval/Routing
            # laufen auf der aktuellen Query
            "history": msg.data.get("history") or [],
            "referential": referential,
            "rewritten_query": rewritten_query,  # für kontext-aufgelöste Auto-Web-Suche
        }, cid)
        self.publish("retrieval_request", "retrieval_request", request, cid)
        if route != ROUTE_GENERAL:
            self.publish("code_retrieval_request", "code_retrieval_request", request, cid)
        if file_paths:
            # Datei-Chunks werden auf der ORIGINAL-Query ausgewählt (der
            # Dateiinhalt ist meist Code/DE — Übersetzung würde die Auswahl
            # verfälschen).
            self.publish("file_request", "file_request",
                         {"query": query, "paths": file_paths}, cid)

        # Web-Recherche: expliziter Trigger hier; Auto-Web (bei GRAUZONE/
        # FALLBACK) fordert der LLMAgent VOR der Generierung an (er kennt die
        # Zone dort schon). Such-Query nutzt bei Rückbezug die umgeschriebene
        # Query (aufgelöster Kontext), sonst das Original.
        if explicit_web:
            web_query = rewritten_query or query
            self.publish("web_request", "web_request",
                         {"query": web_query, "explicit": True}, cid)
            if "web" not in expected:
                expected.append("web")
                self.publish("response_manifest", "response_manifest", {
                    "query": query,
                    "rewritten_query": rewritten_query,
                    "expected": expected,
                    "deadline": settings.response_deadline,
                    "route": route,
                }, cid, reply_to=msg.reply_to)

        # Session speichern nach der Antwort (asynchron, best-effort)
        if session_id:
            self.bus.call_later(0.5, lambda: self._save_session(
                session_id, query, cid))

    def _reject_paths(self, paths: list, cid: str, msg: Message) -> None:
        """Pfad(e) außerhalb der Allowlist → nur Ablehnung, kein Retrieval."""
        self.log.info("Pfade nicht freigegeben: %s", paths)
        self.progress(cid, "path_rejected", ", ".join(paths))
        self.publish("response_manifest", "response_manifest", {
            "query": msg.data.get("query", ""),
            "expected": ["file"],
            "deadline": settings.response_deadline,
        }, cid, reply_to=msg.reply_to)
        self.publish("file_response", "file_response", {
            "paths": [], "rejected": paths, "chunk_count": 0, "chunks": [],
        }, cid)

    def _dispatch_workflow(self, query: str, cid: str, msg: Message) -> None:
        task = query.split(":", 1)[1].strip() if query.lower().startswith("code:") else query
        self.log.info("Code-Task erkannt — Workflow-Pfad.")
        self.progress(cid, "workflow_started", "Code-Workflow wird initiiert")
        self.publish("response_manifest", "response_manifest", {
            "query": query,
            "expected": ["workflow"],
            "deadline": settings.plan_deadline,
        }, cid, reply_to=msg.reply_to)
        self.publish("workflow_request", "workflow_request", {"task": task}, cid)

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

    def _save_session(self, session_id: str, query: str, cid: str) -> None:
        try:
            from collect.session import SessionStore
            store = SessionStore()
            store.add_turn(session_id, query, "", zone="",
                           best_distance=None, duration_s=None)
        except Exception as e:
            self.log.warning("Session-Save fehlgeschlagen: %s", e)
