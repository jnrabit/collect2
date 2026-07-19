"""Qwythos-Serving-Tools — Executor um das kanonische Tool-Schema.

Das Tool-SCHEMA (TOOL_DEFINITIONS, compute_schema_hash) ist die kanonische
Quelle in collect.traces.schema — Training (Traces) und Serving teilen es.
Diese Datei haengt nur die Executor-Funktionen an, die die echte
deterministische Pipeline aufrufen (Serving-Auftrag, nicht Trace-Auftrag).
"""

from __future__ import annotations

import json

from collect.traces.schema import TOOL_DEFINITIONS, compute_schema_hash  # re-export

SYSTEM_PROMPT = (
    "Du bist ein präziser Wissensassistent mit Zugriff auf Tools. "
    "Analysiere jede Nutzer-Frage und entscheide, welche Tools du brauchst. "
    "Rufe Tools in der logischen Reihenfolge auf:\n"
    "1. Bei referenziellen Folgefragen (kurz + Pronomen): rewrite\n"
    "2. Bei deutschen Queries: translate\n"
    "3. Bei mehrteiligen Fragen: decompose\n"
    "4. retrieve für die Suche (das WICHTIGSTE Tool)\n"
    "5. answer für die finale Antwort\n\n"
    "Denke in DEUTSCH. Deine Gedanken (think) sind kurze Entscheidungsbegründungen "
    "(1-3 Sätze, max 80 Tokens), die erklären WARUM du ein Tool aufrufst.\n\n"
    "WICHTIG: Wenn die Frage eine einfache Wissensfrage ist, die direkt beantwortet "
    "werden kann (ohne dass du externe Quellen brauchst), rufe NUR answer auf — "
    "kein retrieve, kein translate, kein decompose."
)


def call_tool(name: str, arguments: dict) -> str:
    """Führt ein Tool aus und gibt das Ergebnis als JSON-String zurück.
    Dies ist die Funktion, die das Modell zur Laufzeit (Inferenz) aufruft —
    identisch zu den Tool-Results in den Trainings-Traces."""
    try:
        if name == "retrieve":
            return _tool_retrieve(arguments)
        elif name == "translate":
            return _tool_translate(arguments)
        elif name == "decompose":
            return _tool_decompose(arguments)
        elif name == "rewrite":
            return _tool_rewrite(arguments)
        elif name == "classify":
            return _tool_classify(arguments)
        elif name == "answer":
            return _tool_answer(arguments)
        elif name == "done":
            return '{"status": "done"}'
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"error": f"unknown tool: {name}"})


# ── Tool-Implementierungen (einfach, deterministisch) ──────────────────

def _tool_retrieve(args: dict) -> str:
    query = str(args.get("query", ""))
    route = str(args.get("route", "general"))
    if not query:
        return json.dumps({"error": "empty query"})
    try:
        from collect.retrieval.embedding import get_backend
        from collect.retrieval.service import VaultSearcher
        from collect.config import settings
        embedder = get_backend()
        hits_list = []
        if route in ("general", "both"):
            searcher = VaultSearcher(
                settings.knowledge_vault_file, settings.knowledge_cache_file,
                settings.knowledge_field_file)
            if searcher.store.ready:
                vecs = [embedder.embed_one(query)]
                merged = searcher.search(vecs, top_k=5, query_text=query)
                for h in searcher.hits(merged, 5):
                    hits_list.append({"title": h.title, "content": h.content[:600],
                                      "distance": round(h.distance, 2)})
        if route in ("code", "both"):
            searcher = VaultSearcher(
                settings.code_vault_file, settings.code_cache_file,
                settings.code_field_file)
            if searcher.store.ready:
                vecs = [embedder.embed_one(query)]
                merged = searcher.search(vecs, top_k=3, query_text=query)
                for h in searcher.hits(merged, 3):
                    hits_list.append({"title": h.title, "content": h.content[:600],
                                      "distance": round(h.distance, 2), "vault": "code"})
        return json.dumps({"hits": hits_list, "count": len(hits_list)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _tool_translate(args: dict) -> str:
    query = str(args.get("query", ""))
    if not query:
        return json.dumps({"translated": query})
    try:
        from collect.retrieval.translator import QueryTranslator
        t = QueryTranslator()
        result = t.translate(query)
        return json.dumps({"translated": result["translated"],
                           "skipped": result["skipped"]})
    except Exception:
        return json.dumps({"translated": query, "skipped": True})


def _tool_decompose(args: dict) -> str:
    query = str(args.get("query", ""))
    if not query:
        return json.dumps({"subqueries": [query]})
    try:
        from collect.retrieval.decomposer import QueryDecomposer
        d = QueryDecomposer()
        result = d.decompose(query)
        return json.dumps({"subqueries": result["subqueries"]})
    except Exception:
        return json.dumps({"subqueries": [query]})


def _tool_rewrite(args: dict) -> str:
    query = str(args.get("query", ""))
    history = args.get("history") or []
    if not query or not history:
        return json.dumps({"rewritten": query, "applied": False})
    try:
        from collect.retrieval.rewriter import rewrite
        result = rewrite(query, history)
        return json.dumps({"rewritten": result.get("rewritten", query),
                           "applied": result.get("applied", False)})
    except Exception:
        return json.dumps({"rewritten": query, "applied": False})


def _tool_classify(args: dict) -> str:
    query = str(args.get("query", ""))
    classification = "knowledge"
    try:
        from collect.agents.orchestrator import is_code_task, is_plan_query
        if is_code_task(query):
            classification = "code"
        elif is_plan_query(query):
            classification = "plan"
    except Exception:
        pass
    return json.dumps({"classification": classification})


def _tool_answer(args: dict) -> str:
    query = str(args.get("query", ""))
    context = str(args.get("context", ""))
    return json.dumps({"status": "generated", "query": query[:100]})
