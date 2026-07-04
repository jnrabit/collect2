"""Code-Workflow: Engine-Lauf, Gates, Erkennung, Agent-Integration."""

import subprocess

import pytest

from collect.agents.orchestrator import is_code_task
from collect.agents.workflow import WorkflowAgent
from collect.bus import InMemoryBus, Message
from collect.config import settings
from collect.workflow.engine import run_workflow
from collect.workflow.execution import extract_code, gate
from collect.workflow.planning import parse_plan

FIB = '''def fib(n):
    """n-te Fibonacci-Zahl (iterativ)."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
'''

FIB_TEST = '''from mathe import fib


def test_fib_basis():
    assert fib(0) == 0
    assert fib(1) == 1


def test_fib_reihe():
    assert [fib(i) for i in range(7)] == [0, 1, 1, 2, 3, 5, 8]
'''


def fake_generate(prompt, system="", on_token=None, **kw):
    if "Software-Planer" in system or "JSON" in system:
        return ('{"strategy": "fib in mathe.py, Test daneben.", '
                '"files": [{"path": "mathe.py", "action": "create", '
                '"description": "iterative fib-Funktion"}, '
                '{"path": "test_mathe.py", "action": "create", '
                '"description": "pytest für fib"}]}')
    if "DATEI: mathe.py" in prompt:
        return f"```python\n{FIB}```"
    if "DATEI: test_mathe.py" in prompt:
        return f"```python\n{FIB_TEST}```"
    return ""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "t"], check=True)
    monkeypatch.setattr(settings, "workflow_repo", r)
    return r


# ── Voller Engine-Lauf ───────────────────────────────────────────────────

def test_full_workflow_creates_verifies_commits(repo):
    ctx = run_workflow("Implementiere eine fib-Funktion mit Test",
                       repo=repo, generate=fake_generate)
    assert ctx.ok
    assert (repo / "mathe.py").exists() and (repo / "test_mathe.py").exists()
    assert ctx.verify["ok"] and not ctx.verify["skipped"]
    assert "2 passed" in ctx.verify["summary"]
    assert ctx.commit.startswith("workflow:")
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "workflow:" in log
    assert "✓ mathe.py" in ctx.report()


def test_failing_tests_block_commit(repo):
    def gen(prompt, system="", **kw):
        if "JSON" in system:
            return ('{"strategy": "s", "files": ['
                    '{"path": "test_kaputt.py", "action": "create", '
                    '"description": "roter Test"}]}')
        return "```python\ndef test_rot():\n    assert 1 == 2\n```"

    ctx = run_workflow("Task", repo=repo, generate=gen)
    assert ctx.verify["ok"] is False
    assert ctx.commit == ""  # kein Commit bei Rot


def test_empty_plan_aborts(repo):
    ctx = run_workflow("Task", repo=repo, generate=lambda p, system="", **kw: "kein json")
    assert ctx.errors and not ctx.changes


# ── Gates ────────────────────────────────────────────────────────────────

def test_gate_blocks_syntax_security_and_symbol_loss():
    assert any("Syntax" in b for b in gate("x.py", "", "def kaputt(:", ""))
    assert any("Security" in b for b in gate("x.py", "", "eval(user_input)\n", ""))
    old = "def keep():\n    pass\n\ndef gone():\n    pass\n"
    assert any("Regression" in b for b in gate("x.py", old, "def keep():\n    pass\n", ""))
    # Plan-Text autorisiert die Löschung (Symbolname + Entfernungs-Verb)
    assert gate("x.py", old, "def keep():\n    pass\n",
                "wir wollen gone entfernen") == []


def test_gate_blocked_write_reaches_report(repo):
    def gen(prompt, system="", **kw):
        if "JSON" in system:
            return ('{"strategy": "s", "files": [{"path": "böse.py", '
                    '"action": "create", "description": "d"}]}')
        return "```python\nexec(payload)\n```"

    ctx = run_workflow("Task", repo=repo, generate=gen)
    assert ctx.changes[0]["status"] == "blocked"
    assert not (repo / "böse.py").exists()
    assert "🛑" in ctx.report()


# ── Plan-Parsing + Erkennung ─────────────────────────────────────────────

def test_parse_plan_rejects_path_escape():
    raw = ('{"strategy": "s", "files": ['
           '{"path": "../ausbruch.py", "action": "create", "description": "d"},'
           '{"path": "/etc/passwd", "action": "create", "description": "d"},'
           '{"path": "ok.py", "action": "create", "description": "d"}]}')
    plan = parse_plan(raw)
    assert [f["path"] for f in plan["files"]] == ["ok.py"]


def test_extract_code_fence_and_fallback():
    assert extract_code("Text\n```python\nx = 1\n```\nmehr") == "x = 1\n"
    assert extract_code("x = 2") == "x = 2\n"


def test_is_code_task():
    assert is_code_task("code: egal was")
    assert is_code_task("Implementiere eine Funktion zur Sortierung")
    assert is_code_task("Fixe den Bug im Parser-Modul")
    assert is_code_task("Schreibe Tests für den Vault")
    assert not is_code_task("Wie funktioniert eine Klasse in Python?")  # Wissensfrage
    assert not is_code_task("Was ist der beste Code-Editor?")
    assert not is_code_task("Erstelle einen Plan für den Umzug")        # Plan, nicht Code


# ── Agent-Integration ────────────────────────────────────────────────────

def test_workflow_agent_publishes_response(repo):
    bus = InMemoryBus(prefix="test.")
    WorkflowAgent(bus, generate_fn=fake_generate).start()
    bus.publish("workflow_request", Message(
        type="workflow_request", data={"task": "Implementiere fib mit Test"},
        correlation_id="w1"))
    out = [m for ch, m in bus.published if ch == "workflow_response"]
    assert len(out) == 1
    assert out[0].data["ok"] is True
    assert out[0].data["committed"] is True
    assert "Code-Workflow für:" in out[0].data["report"]


# ── Reparatur-Runde ──────────────────────────────────────────────────────

def test_repair_round_fixes_missing_import(repo):
    """Simuliert den real beobachteten 7B-Fehler: Testdatei ohne Import."""
    state = {"repaired": False}

    def gen(prompt, system="", **kw):
        if "JSON" in system:
            return ('{"strategy": "fib + Test", "files": ['
                    '{"path": "mathe.py", "action": "create", "description": "fib"},'
                    '{"path": "test_mathe.py", "action": "create", "description": "test"}]}')
        if "repariere GENAU EINE Datei" in prompt:
            state["repaired"] = True
            return f"PFAD: test_mathe.py\n```python\n{FIB_TEST}```"
        if "DATEI: mathe.py" in prompt:
            return f"```python\n{FIB}```"
        if "DATEI: test_mathe.py" in prompt:
            # Fehler: Import fehlt (wie im echten Lauf beobachtet)
            return "```python\ndef test_fib_basis():\n    assert fib(0) == 0\n```"
        return ""

    ctx = run_workflow("fib implementieren", repo=repo, generate=gen)
    assert state["repaired"] is True
    assert ctx.verify["ok"] is True
    assert ctx.commit  # nach Reparatur grün → Commit
    assert any(c["status"] == "repaired" for c in ctx.changes)


def test_repair_gives_up_gracefully(repo):
    def gen(prompt, system="", **kw):
        if "JSON" in system:
            return ('{"strategy": "s", "files": [{"path": "test_rot.py", '
                    '"action": "create", "description": "d"}]}')
        if "repariere" in prompt:
            return "kann ich nicht"  # keine PFAD-Zeile
        return "```python\ndef test_rot():\n    assert False\n```"

    ctx = run_workflow("Task", repo=repo, generate=gen)
    assert ctx.verify["ok"] is False
    assert ctx.commit == ""
    assert any("Reparatur" in e for e in ctx.errors)
