"""E2E: user_query → user_response über den kompletten Agenten-Stack —
auf dem InMemoryBus, mit Mini-Vault, Fake-Embedder und Fake-LLM.
Deterministisch, ohne Redis/Ollama/torch (DESIGN.md §5.3)."""

import pytest

from collect.agents.decision import DecisionAgent
from collect.agents.executor import ExecutorAgent
from collect.agents.llm import LLMAgent
from collect.agents.orchestrator import OrchestratorAgent, is_plan_query
from collect.agents.planning import PlanningAgent
from collect.agents.response import ResponseAgent
from collect.agents.retrieval import RetrievalAgent
from collect.bus import InMemoryBus, Message
from collect.config import settings
from collect.retrieval.router import CodeRouter
from collect.retrieval.zones import ZONE_FALLBACK
from tests.conftest import FakeEmbedder
from tests.test_service import _searcher


def fake_llm(prompt, system="", timeout=None, **kw):
    assert "QUELLEN:" in prompt
    return "Geerdete Antwort basierend auf den Quellen."


def fake_decide(prompt, system="", timeout=None, **kw):
    return ('[{"id":"step_1","description":"Workspace listen",'
            '"action":"list_files"}]')


@pytest.fixture
def pipeline(mini_store, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "beispiel.txt").write_text("x")
    monkeypatch.setattr(settings, "executor_workspace", ws)
    monkeypatch.setattr(settings, "executor_read_roots", [tmp_path])

    bus = InMemoryBus(prefix="test.")
    emb = FakeEmbedder()
    agents = [
        OrchestratorAgent(bus, CodeRouter(emb.embed_one, centroid=None)),
        RetrievalAgent(bus, _searcher(mini_store), emb, kind="retrieval"),
        LLMAgent(bus, generate_fn=fake_llm),
        DecisionAgent(bus, generate_fn=fake_decide),
        PlanningAgent(bus),
        ExecutorAgent(bus),
        ResponseAgent(bus),
    ]
    for a in agents:
        a.start()
    return bus


def _ask(bus, query, cid="e2e-1"):
    bus.publish("user_query", Message(
        type="user_query", data={"query": query},
        correlation_id=cid, reply_to="user_response.test"))
    return [m for ch, m in bus.published if ch == "user_response.test"]


def test_knowledge_query_end_to_end(pipeline):
    out = _ask(pipeline, "TLS negotiates keys")
    assert len(out) == 1
    data = out[0].data
    assert "Geerdete Antwort" in data["text"]
    assert "General-Vault" in data["text"]
    assert data["meta"]["finalize_reason"] == "complete"
    assert data["meta"]["zone"] in ("TRUST", "GRAUZONE")


def test_plan_query_end_to_end(pipeline):
    out = _ask(pipeline, "Erstelle einen Plan für den Workspace", cid="e2e-2")
    assert len(out) == 1
    data = out[0].data
    assert "Ausführungsergebnis" in data["text"]
    assert "✓ Workspace listen" in data["text"]
    assert "beispiel.txt" in data["text"]
    assert data["meta"]["executed"] is True


def test_fallback_query_suppresses_llm(pipeline, monkeypatch):
    # Schwellen so eng, dass der Mini-Vault-Treffer sicher FALLBACK ist
    monkeypatch.setattr(settings, "vault_trust_threshold", 0.1)
    monkeypatch.setattr(settings, "vault_soft_max_distance", 0.2)
    # Auto-Web aus: dieser Test prüft die Suppression, nicht die Web-Recherche
    # (unabhängig von einer lokalen .env mit COLLECT_WEB_SEARCH_AUTO=true)
    monkeypatch.setattr(settings, "web_search_auto", False)
    out = _ask(pipeline, "voellig anderes unbekanntes thema", cid="e2e-3")
    data = out[0].data
    assert data["meta"]["zone"] == ZONE_FALLBACK
    assert "außerhalb des indizierten Wissensbereichs" in data["text"]
    assert "Geerdete Antwort" not in data["text"]


def test_progress_events_emitted(pipeline):
    _ask(pipeline, "TLS negotiates keys", cid="e2e-4")
    stages = [m.data["stage"] for ch, m in pipeline.published
              if ch == "progress" and m.correlation_id == "e2e-4"]
    assert "query_received" in stages
    assert "routing" in stages
    assert "retrieval_done" in stages


def test_is_plan_query_keywords():
    assert is_plan_query("Erstelle einen Plan für X")
    assert is_plan_query("Welche Schritte brauche ich?")
    assert not is_plan_query("Was ist TLS?")
