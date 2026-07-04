"""Gesprächskontext: Orchestrator → LLM-Prompt, WS-Session-Historie."""

import pytest

from collect.agents.llm import LLMAgent
from collect.agents.orchestrator import OrchestratorAgent
from collect.bus import InMemoryBus, Message
from collect.retrieval.router import CodeRouter
from tests.conftest import FakeEmbedder


def test_orchestrator_forwards_history():
    bus = InMemoryBus(prefix="test.")
    emb = FakeEmbedder()
    OrchestratorAgent(bus, CodeRouter(emb.embed_one, centroid=None)).start()
    bus.publish("user_query", Message(
        type="user_query",
        data={"query": "und wie genau?",
              "history": [{"q": "Was ist TLS?", "a": "Ein Protokoll."}]},
        correlation_id="h1"))
    llm_reqs = [m for ch, m in bus.published if ch == "llm_request"]
    assert len(llm_reqs) == 1
    assert llm_reqs[0].data["history"] == [{"q": "Was ist TLS?", "a": "Ein Protokoll."}]


def test_llm_prompt_includes_history():
    bus = InMemoryBus(prefix="test.")
    prompts = []

    def capture(prompt, system="", on_token=None, **kw):
        prompts.append(prompt)
        return "ok", {}

    LLMAgent(bus, generate_fn=capture).start()
    bus.publish("llm_request", Message(
        type="llm_request",
        data={"query": "und wie genau?", "original_query": "und wie genau?",
              "needs": ["retrieval"],
              "history": [{"q": "Was ist TLS?", "a": "Ein Verschlüsselungs-Protokoll."}]},
        correlation_id="h2"))
    bus.publish("retrieval_response", Message(
        type="retrieval_response",
        data={"hits": [], "zone": "TRUST", "best_distance": 40.0, "count": 0},
        correlation_id="h2"))

    assert len(prompts) == 1
    assert "BISHERIGES GESPRÄCH" in prompts[0]
    assert "Was ist TLS?" in prompts[0]
    assert "FRAGE: und wie genau?" in prompts[0]


def test_llm_prompt_without_history_has_no_block():
    bus = InMemoryBus(prefix="test.")
    prompts = []

    def capture(prompt, system="", on_token=None, **kw):
        prompts.append(prompt)
        return "ok", {}

    LLMAgent(bus, generate_fn=capture).start()
    bus.publish("llm_request", Message(
        type="llm_request",
        data={"query": "Was ist TLS?", "needs": ["retrieval"]},
        correlation_id="h3"))
    bus.publish("retrieval_response", Message(
        type="retrieval_response",
        data={"hits": [], "zone": "TRUST", "best_distance": 40.0, "count": 0},
        correlation_id="h3"))
    assert "BISHERIGES GESPRÄCH" not in prompts[0]


def test_ws_session_accumulates_history(monkeypatch, tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from collect import api
    from collect.config import settings

    monkeypatch.setattr(settings, "ossifikat_db", tmp_path / "o.db")
    seen_histories = []

    def fake_stream(query, timeout=None, history=None):
        seen_histories.append(history or [])
        yield ("answer", {"text": f"Antwort auf {query}", "meta": {"zone": "TRUST"}})

    import collect.client
    monkeypatch.setattr(collect.client, "stream", fake_stream)

    c = TestClient(api.create_app())
    with c.websocket_connect("/ws/chat") as ws:
        ws.send_json({"query": "Frage eins"})
        assert ws.receive_json()["type"] == "answer"
        ws.send_json({"query": "Frage zwei"})
        assert ws.receive_json()["type"] == "answer"

    assert seen_histories[0] == []
    assert seen_histories[1] == [{"q": "Frage eins", "a": "Antwort auf Frage eins"}]
