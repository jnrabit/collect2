"""Robustheits-/Security-Batch aus der externen Review (echte Randfälle)."""

import pytest

from collect.agents.executor import MAX_READ_BYTES, StepError, execute_action
from collect.config import settings


# ── #5: on_token-Callback-Fehler bricht Generierung nicht ab ──────────────

def test_streaming_survives_on_token_failure():
    import json
    from unittest import mock
    from collect.agents import ollama
    from tests.test_streaming import FakeStreamResponse

    chunks = [{"response": "Teil eins "}, {"response": "Teil zwei"}, {"done": True}]

    def boom(_delta):
        raise RuntimeError("Bus temporär weg")

    with mock.patch.object(ollama.requests, "post",
                           return_value=FakeStreamResponse(chunks)):
        # on_token wirft bei jedem Chunk → Generierung läuft trotzdem durch
        text, _ = ollama.generate_streaming("p", on_token=boom)
    assert text == "Teil eins Teil zwei"


# ── #28: execute nutzt shlex (Quotes) ─────────────────────────────────────

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(settings, "executor_workspace", ws)
    monkeypatch.setattr(settings, "executor_read_roots", [tmp_path])
    monkeypatch.setattr(settings, "executor_allow_execute", True)
    return ws


def test_execute_respects_quotes(workspace):
    # 'echo "a b"' → ein Arg "a b", nicht zwei
    out = execute_action('execute:echo "a b c"')
    assert out == "a b c"


def test_execute_empty_command_rejected(workspace):
    with pytest.raises(StepError, match="kein Befehl"):
        execute_action("execute:   ")


def test_execute_unbalanced_quotes_rejected(workspace):
    with pytest.raises(StepError, match="Ungültige Argumente"):
        execute_action('execute:echo "offen')


# ── #29: read: Dateigrößen-Limit ──────────────────────────────────────────

def test_read_rejects_oversized_file(workspace, monkeypatch, tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * (MAX_READ_BYTES + 10))
    with pytest.raises(StepError, match="zu groß"):
        execute_action(f"read:{big}")


def test_read_normal_file_ok(workspace, tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("kurzer inhalt")
    assert "kurzer inhalt" in execute_action(f"read:{f}")


# ── #10: Message.to_json überlebt nicht-JSON-Feld ─────────────────────────

def test_message_to_json_coerces_nonjson():
    from pathlib import Path
    from collect.bus import Message
    m = Message(type="t", data={"p": Path("/tmp/x")})  # Path ist nicht JSON
    js = m.to_json()  # darf nicht werfen
    assert "/tmp/x" in js


# ── #2: Heartbeat ohne Redis-Bus (InMemoryBus) crasht nicht ───────────────

def test_heartbeat_skips_without_redis(monkeypatch):
    from collect.bus import InMemoryBus
    from collect.agents.runner import _start_heartbeat

    class Dummy:
        name = "x"

    bus = InMemoryBus(prefix="t.")
    assert not hasattr(bus, "redis")
    monkeypatch.setattr(settings, "heartbeat_interval", 9999)
    t = _start_heartbeat(bus, [Dummy()])  # darf nicht crashen
    assert t.is_alive()


# ── #6: _early-Einträge altern per TTL raus ───────────────────────────────

def test_early_entries_expire_by_ttl(monkeypatch):
    import time
    from collect.agents.llm import LLMAgent
    from collect.bus import InMemoryBus, Message

    agent = LLMAgent(InMemoryBus(prefix="t."))
    agent._EARLY_TTL = 0.0  # sofort abgelaufen
    handler = agent.on_contribution("retrieval")
    handler(Message(type="retrieval_response", data={"zone": "TRUST"},
                    correlation_id="verwaist"))
    # nächster Early-Beitrag triggert Eviction des abgelaufenen
    handler(Message(type="retrieval_response", data={"zone": "TRUST"},
                    correlation_id="neu"))
    assert "verwaist" not in agent._early


# ── #4: /api/facts graceful ohne Ossifikat-Submodul ───────────────────────

def test_facts_graceful_without_ossifikat(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from collect import api

    db = tmp_path / "o.db"
    db.write_text("x")  # existiert, aber Import wird gemockt zu ImportError
    monkeypatch.setattr(settings, "ossifikat_db", db)

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "ossifikat.store":
            raise ImportError("submodule not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    c = TestClient(api.create_app())
    r = c.get("/api/facts")
    assert r.status_code == 200
    assert r.json()["facts"] == []
