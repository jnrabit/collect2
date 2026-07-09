"""Tests für workflow.validator — Cross-File-Checks und Plan-Drift."""

import tempfile
from pathlib import Path

from collect.workflow.context import WorkflowContext
from collect.workflow.validator import (
    extract_imports,
    extract_top_level_symbols,
    find_cross_file_issues,
    find_hallucinated_files,
    find_plan_drift,
    find_test_gaps,
    validate_workflow,
)


def test_extract_top_level_symbols():
    src = '''
def foo():
    pass

class Bar:
    pass

CONSTANT = 42
lower = "x"
'''
    syms = extract_top_level_symbols(src)
    assert syms["functions"] == {"foo"}
    assert syms["classes"] == {"Bar"}
    assert syms["constants"] == {"CONSTANT"}


def test_extract_imports():
    src = '''
import os
from pathlib import Path
from collect.config import settings, display_name
from typing import *
'''
    imports = extract_imports(src)
    assert "os" in imports
    assert imports["os"] == set()
    assert "pathlib" in imports
    assert imports["pathlib"] == {"Path"}
    assert "collect.config" in imports
    assert imports["collect.config"] == {"settings", "display_name"}
    assert "typing" in imports


def test_cross_file_no_issues():
    files = {
        "utils.py": "def add(a, b): return a + b\ndef sub(a, b): return a - b",
        "test_utils.py": "from utils import add\ndef test_add(): assert add(1, 2) == 3",
    }
    issues = find_cross_file_issues(files)
    assert len(issues) == 0


def test_cross_file_missing_export():
    files = {
        "utils.py": "def add(a, b): return a + b",
        "test_utils.py": "from utils import add, multiply",
    }
    issues = find_cross_file_issues(files)
    assert len(issues) >= 1
    assert any("multiply" in i["detail"] for i in issues)


def test_find_hallucinated_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("x = 1")
    plan_files = [
        {"path": "real.py", "action": "modify", "description": "update real"},
        {"path": "ghost.py", "action": "modify", "description": "modify ghost"},
        {"path": "new.py", "action": "create", "description": "new file"},
    ]
    issues = find_hallucinated_files(plan_files, repo)
    assert len(issues) == 1
    assert issues[0]["file"] == "ghost.py"


def test_find_plan_drift():
    plan_files = [
        {"path": "calc.py", "action": "create",
         "description": "Implementiere add und subtract Funktionen"},
    ]
    generated = {
        "calc.py": "def add(a, b): return a + b\ndef multiply(a, b): return a * b",
    }
    issues = find_plan_drift(plan_files, generated)
    drift_names = [i for i in issues if i["kind"] == "plan_drift"]
    assert any("multiply" in i["detail"] for i in drift_names)


def test_find_test_gaps():
    files = {
        "calc.py": "def add(a, b): return a + b\ndef sub(a, b): return a - b",
        "test_other.py": "def test_foo(): pass",
    }
    issues = find_test_gaps(files)
    assert len(issues) >= 1
    assert issues[0]["file"] == "calc.py"
    assert "missing_tests" in issues[0]["kind"]


def test_find_test_gaps_ok():
    files = {
        "calc.py": "def add(a, b): return a + b",
        "test_calc.py": "from calc import add\ndef test_add(): pass",
    }
    issues = find_test_gaps(files)
    assert len(issues) == 0


def test_validate_workflow_integration(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def greet(): return 'hi'")
    (repo / "test_src.py").write_text("from src import greet\ndef test_greet(): pass")

    from dataclasses import dataclass, field
    ctx = WorkflowContext(task="greet function", repo=repo)
    ctx.plan = {"strategy": "Implementiere greet", "files": [
        {"path": "src.py", "action": "create", "description": "Implementiere greet Funktion"},
        {"path": "test_src.py", "action": "create", "description": "Test für greet"},
    ]}
    ctx.changes = [
        {"path": "src.py", "status": "written", "detail": "ok"},
        {"path": "test_src.py", "status": "written", "detail": "ok"},
    ]

    issues = validate_workflow(ctx)
    assert not any(i["kind"] == "hallucinated_file" for i in issues)
