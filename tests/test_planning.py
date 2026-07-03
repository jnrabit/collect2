"""Plan→Decide→Act auf dem InMemoryBus: Kaskade, Idempotenz, Timeouts."""

import pytest

from collect.agents.decision import DecisionAgent
from collect.agents.executor import ExecutorAgent
from collect.agents.planning import PlanningAgent
from collect.bus import InMemoryBus, Message
from collect.config import settings


@pytest.fixture
def stack(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(settings, "executor_workspace", ws)
    monkeypatch.setattr(settings, "executor_read_roots", [tmp_path])
    bus = InMemoryBus(prefix="test.")
    planning = PlanningAgent(bus)
    executor = ExecutorAgent(bus)
    return bus, planning, executor, ws


def _start(bus, *agents):
    for a in agents:
        a.start()


def _plan_request(bus, query, cid="cid-1"):
    bus.publish("planning_request", Message(
        type="planning_request",
        data={"action": "create_and_execute", "query": query},
        correlation_id=cid))


def _planning_responses(bus):
    return [m for ch, m in bus.published if ch == "planning_response"]


def test_executable_plan_runs_steps(stack):
    bus, planning, executor, ws = stack

    def fake_generate(prompt, system="", timeout=None):
        return ('[{"id":"step_1","description":"Datei schreiben",'
                '"action":"write:hallo.txt|Hallo"},'
                '{"id":"step_2","description":"Nachdenken","action":""}]')

    _start(bus, planning, executor, DecisionAgent(bus, generate_fn=fake_generate))
    _plan_request(bus, "Erstelle eine Hallo-Datei")

    out = _planning_responses(bus)
    assert len(out) == 1
    data = out[0].data
    assert data["executed"] is True
    assert data["status"] == "completed"
    assert (ws / "hallo.txt").read_text() == "Hallo"
    assert "✓ Datei schreiben" in data["message"]
    assert out[0].correlation_id == "cid-1"


def test_informational_plan_returned_as_content(stack):
    bus, planning, executor, _ = stack

    def fake_generate(prompt, system="", timeout=None):
        return ('[{"id":"step_1","description":"Recherchieren","action":""},'
                '{"id":"step_2","description":"Zusammenfassen","action":""}]')

    _start(bus, planning, executor, DecisionAgent(bus, generate_fn=fake_generate))
    _plan_request(bus, "Plane eine Recherche")

    out = _planning_responses(bus)
    assert len(out) == 1
    data = out[0].data
    assert data["executed"] is False
    assert "1. Recherchieren" in data["message"]
    assert "2. Zusammenfassen" in data["message"]


def test_failed_step_reported_and_plan_continues(stack):
    bus, planning, executor, ws = stack

    def fake_generate(prompt, system="", timeout=None):
        return ('[{"id":"step_1","description":"Verbotenes lesen",'
                '"action":"read:/etc/shadow"},'
                '{"id":"step_2","description":"Datei schreiben",'
                '"action":"write:ok.txt|geht"}]')

    _start(bus, planning, executor, DecisionAgent(bus, generate_fn=fake_generate))
    _plan_request(bus, "Erstelle einen gemischten Plan")

    out = _planning_responses(bus)
    data = out[0].data
    assert data["status"] == "completed_with_errors"
    assert "✗ Verbotenes lesen" in data["message"]
    assert (ws / "ok.txt").exists()  # Plan lief nach dem Fehler weiter


def test_decide_timeout_finalizes_empty_plan(stack):
    bus, planning, _, _ = stack
    _start(bus, planning)  # KEIN DecisionAgent → keine Antwort
    _plan_request(bus, "Plane etwas")
    assert _planning_responses(bus) == []
    bus.run_due(now=float("inf"))  # Decide-Deadline feuert
    out = _planning_responses(bus)
    assert len(out) == 1
    assert "Decide-Timeout" in out[0].data["message"]


def test_step_timeout_marks_and_continues(stack):
    bus, planning, _, _ = stack

    def fake_generate(prompt, system="", timeout=None):
        return '[{"id":"step_1","description":"Hängt","action":"read:/x"}]'

    _start(bus, planning, DecisionAgent(bus, generate_fn=fake_generate))
    # KEIN Executor → step_response bleibt aus
    _plan_request(bus, "Plane das Hängen")
    assert _planning_responses(bus) == []
    bus.run_due(now=float("inf"))  # Schritt-Deadline feuert
    out = _planning_responses(bus)
    assert len(out) == 1
    assert "✗ Hängt" in out[0].data["message"]


def test_duplicate_step_response_not_double_counted(stack):
    bus, planning, executor, _ = stack

    def fake_generate(prompt, system="", timeout=None):
        return '[{"id":"step_1","description":"schreiben","action":"write:a.txt|x"}]'

    _start(bus, planning, executor, DecisionAgent(bus, generate_fn=fake_generate))
    _plan_request(bus, "Erstelle Plan")
    assert len(_planning_responses(bus)) == 1

    # Nachzügler-Duplikat der Schritt-Antwort → darf nichts mehr auslösen
    bus.publish("step_response", Message(
        type="step_response",
        data={"plan_id": _planning_responses(bus)[0].data["plan_id"],
              "step_id": "step_1", "status": "finished"},
        correlation_id="cid-1"))
    assert len(_planning_responses(bus)) == 1
