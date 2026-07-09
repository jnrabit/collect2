"""Tests für workflow.sandbox — isolierte Code-Ausführung."""

from pathlib import Path

from collect.workflow.sandbox import sandbox_pytest, sandbox_run


def test_sandbox_pytest_pass(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b): return a + b\n")
    (repo / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add(): assert add(1, 2) == 3\n"
        "def test_add_neg(): assert add(-1, 1) == 0\n"
    )
    result = sandbox_pytest(repo, timeout=30)
    assert result["sandboxed"] is True
    assert result["ok"] is True
    assert "passed" in result["summary"]


def test_sandbox_pytest_fail(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b): return a + b\n")
    (repo / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add(): assert add(1, 2) == 4\n"
    )
    result = sandbox_pytest(repo, timeout=30)
    assert result["ok"] is False
    assert "failed" in result["summary"]


def test_sandbox_pytest_no_tests(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b): return a + b\n")
    result = sandbox_pytest(repo, timeout=30)
    assert "no tests ran" in result["summary"].lower() or "0 passed" in result["summary"]


def test_sandbox_run_ok(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = sandbox_run("print('hello sandbox')", repo, timeout=10)
    assert result["ok"] is True
    assert "hello sandbox" in result["stdout"]


def test_sandbox_run_timeout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = sandbox_run(
        "import time; time.sleep(60)",
        repo, timeout=1.0,
    )
    assert result["ok"] is False
    assert result["exit_code"] == -9
