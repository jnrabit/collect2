"""REST-API (TestClient), Status-Schicht, REPL-Review-Flow."""

import time

import pytest

from collect.config import settings

fastapi = pytest.importorskip("fastapi")


# ── Status ───────────────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self):
        self.h = {}

    def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return self.h.get(key, {})


def test_heartbeat_and_status_roundtrip():
    from collect.status import get_agent_status, write_heartbeat
    r = FakeRedis()
    write_heartbeat(r, "orchestrator")
    status = get_agent_status(r)
    assert status["orchestrator"]["alive"] is True
    assert status["orchestrator"]["age_s"] < 1.0


def test_stale_heartbeat_detected():
    from collect.status import get_agent_status, status_key
    r = FakeRedis()
    r.hset(status_key(), "llm", time.time() - 3600)
    assert get_agent_status(r)["llm"]["alive"] is False


# ── API ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from collect import api

    monkeypatch.setattr(settings, "ossifikat_db", tmp_path / "ossifikat.db")
    return TestClient(api.create_app()), monkeypatch


def test_health_down_without_agents(client, monkeypatch):
    c, mp = client
    import collect.status
    mp.setattr(collect.status, "get_agent_status", lambda *a, **k: {})
    resp = c.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "down"


def test_query_endpoint_calls_ask(client, monkeypatch):
    c, mp = client
    import collect.client
    mp.setattr(collect.client, "ask",
               lambda q, timeout=None: {"text": f"Antwort auf: {q}", "meta": {"zone": "TRUST"}})
    resp = c.post("/api/query", json={"query": "Testfrage"})
    assert resp.status_code == 200
    assert resp.json()["text"] == "Antwort auf: Testfrage"


def test_query_validates_input(client):
    c, _ = client
    assert c.post("/api/query", json={"query": ""}).status_code == 422
    assert c.post("/api/query", json={}).status_code == 422


def test_query_timeout_is_504(client, monkeypatch):
    c, mp = client
    import collect.client
    mp.setattr(collect.client, "ask",
               lambda q, timeout=None: {"text": "⚠️ Timeout", "meta": {"timeout": True}})
    assert c.post("/api/query", json={"query": "x"}).status_code == 504


def test_facts_endpoint(client, tmp_path):
    c, _ = client
    from ossifikat.store import OssifikatStore
    s = OssifikatStore(str(tmp_path / "ossifikat.db"))
    tid = s.add_staging("A", "ist", "B", source="test")
    s.confirm(tid, confirmed_by="test")
    s.close()
    facts = c.get("/api/facts").json()["facts"]
    assert len(facts) == 1 and facts[0]["subject"] == "A"


# ── REPL-Review ──────────────────────────────────────────────────────────

def test_repl_review_confirms_and_rejects(tmp_path, monkeypatch):
    from collect.repl import cmd_review
    monkeypatch.setattr(settings, "ossifikat_db", tmp_path / "ossifikat.db")

    from ossifikat.store import OssifikatStore
    s = OssifikatStore(str(tmp_path / "ossifikat.db"))
    s.add_staging("Gut", "ist", "wahr", source="test")
    s.add_staging("Schlecht", "ist", "falsch", source="test")
    s.close()

    answers = iter(["j", "n"])
    out = []
    result = cmd_review(input_fn=lambda _: next(answers), print_fn=out.append)
    assert "1 verbürgt, 1 verworfen" in result

    s = OssifikatStore(str(tmp_path / "ossifikat.db"))
    confirmed = s.query()
    assert len(confirmed) == 1 and confirmed[0].subject == "Gut"
    assert s.list_staging() == []
    s.close()


# ── Web-Chat ─────────────────────────────────────────────────────────────

def test_chat_page_served(client):
    c, _ = client
    resp = c.get("/chat")
    assert resp.status_code == 200
    assert "collect2" in resp.text and "ws/chat" in resp.text


def test_root_redirects_to_chat(client):
    c, _ = client
    resp = c.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/chat"


def test_ws_chat_streams_progress_then_answer(client, monkeypatch):
    c, mp = client
    import collect.client

    def fake_stream(query, timeout=None):
        yield ("progress", {"stage": "routing", "detail": "general"})
        yield ("answer", {"text": f"Antwort: {query}", "meta": {"zone": "TRUST"}})

    mp.setattr(collect.client, "stream", fake_stream)
    with c.websocket_connect("/ws/chat") as ws:
        ws.send_json({"query": "Testfrage"})
        first = ws.receive_json()
        assert first == {"type": "progress",
                         "data": {"stage": "routing", "detail": "general"}}
        second = ws.receive_json()
        assert second["type"] == "answer"
        assert second["data"]["text"] == "Antwort: Testfrage"


def test_ws_chat_rejects_empty_query(client):
    c, _ = client
    with c.websocket_connect("/ws/chat") as ws:
        ws.send_json({"query": "   "})
        assert ws.receive_json()["type"] == "error"
