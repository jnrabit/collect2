"""Rang 5: Feature-Flags für bisher fest verdrahtete Code-Zweige.

workflow_enabled / planning_enabled (Orchestrator-Dispatch),
web_search_auto_with_file (Auto-Web trotz Datei-Kontext),
fallback_suppress (zentraler Halluzinations-Schutz, dedupliziert
in zones.fallback_suppressed).
"""

from collect.agents.llm import LLMAgent
from collect.agents.orchestrator import OrchestratorAgent
from collect.agents.response import synthesize
from collect.bus import InMemoryBus, Message
from collect.config import settings
from collect.retrieval.router import CodeRouter
from collect.retrieval.zones import fallback_suppressed
from tests.conftest import FakeEmbedder


def _channels(query):
    bus = InMemoryBus(prefix="test.")
    emb = FakeEmbedder()
    OrchestratorAgent(bus, CodeRouter(emb.embed_one, centroid=None)).start()
    bus.publish("user_query", Message(
        type="user_query", data={"query": query}, correlation_id="f1"))
    return {ch for ch, _ in bus.published}


# ── workflow_enabled / planning_enabled ──────────────────────────────────

def test_workflow_disabled_routes_to_retrieval(monkeypatch):
    monkeypatch.setattr(settings, "workflow_enabled", False)
    chans = _channels("Implementiere eine Funktion zur Sortierung")
    assert "workflow_request" not in chans
    assert "retrieval_request" in chans  # läuft als normale Wissensfrage


def test_workflow_enabled_dispatches(monkeypatch):
    monkeypatch.setattr(settings, "workflow_enabled", True)
    chans = _channels("Implementiere eine Funktion zur Sortierung")
    assert "workflow_request" in chans
    assert "retrieval_request" not in chans


def test_planning_disabled_routes_to_retrieval(monkeypatch):
    monkeypatch.setattr(settings, "planning_enabled", False)
    chans = _channels("Erstelle einen Plan mit allen Schritten")
    assert "planning_request" not in chans
    assert "retrieval_request" in chans


# ── fallback_suppressed: zentrale Entscheidung ───────────────────────────

def test_fallback_suppressed_logic(monkeypatch):
    monkeypatch.setattr(settings, "fallback_suppress", True)
    assert fallback_suppressed(True, grounded=False) is True
    assert fallback_suppressed(True, grounded=True) is False
    assert fallback_suppressed(False, grounded=False) is False
    monkeypatch.setattr(settings, "fallback_suppress", False)
    assert fallback_suppressed(True, grounded=False) is False


def test_fallback_suppress_off_llm_generates(monkeypatch):
    monkeypatch.setattr(settings, "fallback_suppress", False)
    monkeypatch.setattr(settings, "web_search_auto", False)
    bus = InMemoryBus(prefix="test.")
    LLMAgent(bus, generate_fn=lambda p, system="", on_token=None, **k:
             ("ungeerdete Antwort", {})).start()
    bus.publish("llm_request", Message(
        type="llm_request", data={"query": "q", "needs": ["retrieval"]},
        correlation_id="f2"))
    bus.publish("retrieval_response", Message(
        type="retrieval_response",
        data={"zone": "FALLBACK", "best_distance": 80.0, "count": 0, "hits": []},
        correlation_id="f2"))
    final = [m for ch, m in bus.published if ch == "llm_response"]
    assert final and final[0].data["skipped"] is False
    assert final[0].data["content"] == "ungeerdete Antwort"


def test_fallback_suppress_off_response_warns(monkeypatch):
    monkeypatch.setattr(settings, "fallback_suppress", False)
    text, meta = synthesize({
        "query": "q", "expected": {"retrieval", "llm"},
        "contribs": {
            "retrieval": {"zone": "FALLBACK", "best_distance": 80.0,
                          "count": 0, "hits": []},
            "llm": {"content": "ungeerdete Antwort", "facts_used": 0},
        },
        "reply_to": "r", "finalized": False, "started_at": 0,
    })
    assert "ungeerdete Antwort" in text          # Antwort NICHT verschluckt
    assert "NICHT vault-geerdet" in text         # aber klar gekennzeichnet
    assert meta["zone"] == "FALLBACK"


def test_fallback_suppress_on_keeps_old_behavior():
    # Default: Antwort wird durch den Außerhalb-Hinweis ersetzt
    text, _ = synthesize({
        "query": "q", "expected": {"retrieval", "llm"},
        "contribs": {
            "retrieval": {"zone": "FALLBACK", "best_distance": 80.0,
                          "count": 0, "hits": []},
            "llm": {"content": "sollte nicht erscheinen", "facts_used": 0},
        },
        "reply_to": "r", "finalized": False, "started_at": 0,
    })
    assert "außerhalb des indizierten Wissensbereichs" in text
    assert "sollte nicht erscheinen" not in text


# ── web_search_auto_with_file ────────────────────────────────────────────

def _auto_web_state(with_file):
    contribs = {"retrieval": {"zone": "GRAUZONE", "best_distance": 55.0,
                              "count": 1, "hits": []}}
    if with_file:
        contribs["file"] = {"chunks": ["c1"], "chunk_count": 1}
    return {"contribs": contribs, "web_requested": False, "query": "q"}


def test_file_context_suppresses_auto_web(monkeypatch):
    monkeypatch.setattr(settings, "web_search_enabled", True)
    monkeypatch.setattr(settings, "web_search_auto", True)
    monkeypatch.setattr(settings, "web_search_auto_with_file", False)
    agent = LLMAgent(InMemoryBus(prefix="t."))
    assert agent._should_auto_web(_auto_web_state(with_file=True)) is False
    assert agent._should_auto_web(_auto_web_state(with_file=False)) is True


def test_auto_web_with_file_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "web_search_enabled", True)
    monkeypatch.setattr(settings, "web_search_auto", True)
    monkeypatch.setattr(settings, "web_search_auto_with_file", True)
    agent = LLMAgent(InMemoryBus(prefix="t."))
    assert agent._should_auto_web(_auto_web_state(with_file=True)) is True
