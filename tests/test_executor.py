"""Executor: Aktions-Grammatik, Sicherheitsgrenzen, Pre-Write-Gate."""

import pytest

from collect.agents.executor import StepError, execute_action
from collect.config import settings


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(settings, "executor_workspace", ws)
    monkeypatch.setattr(settings, "executor_read_roots", [tmp_path])
    return ws


def test_write_and_read(workspace):
    result = execute_action("write:notiz.txt|Hallo Welt")
    assert "notiz.txt" in result
    assert execute_action(f"read:{workspace}/notiz.txt") == "Hallo Welt"


def test_read_outside_roots_blocked(workspace):
    with pytest.raises(StepError, match="außerhalb"):
        execute_action("read:/etc/passwd")


def test_write_outside_workspace_blocked(workspace, tmp_path):
    outside = tmp_path / "woanders.txt"
    with pytest.raises(StepError, match="außerhalb"):
        execute_action(f"write:{outside}|x")


def test_write_python_syntax_gate(workspace):
    with pytest.raises(StepError, match="Syntax"):
        execute_action("write:kaputt.py|def broken(:")


def test_write_regression_gate_blocks_symbol_loss(workspace):
    execute_action("write:mod.py|def keep():\n    pass\n\ndef gone():\n    pass\n")
    with pytest.raises(StepError, match="Regression-Guard"):
        execute_action("write:mod.py|def keep():\n    pass\n")


def test_execute_disabled_by_default(workspace):
    with pytest.raises(StepError, match="deaktiviert"):
        execute_action("execute:echo hi")


def test_execute_when_enabled(workspace, monkeypatch):
    monkeypatch.setattr(settings, "executor_allow_execute", True)
    assert execute_action("execute:echo hallo") == "hallo"


def test_list_and_info_and_dirs(workspace):
    execute_action("create_directory:unterordner")
    assert (workspace / "unterordner").is_dir()
    listing = execute_action(f"list_files:{workspace}")
    assert "unterordner/" in listing
    info = execute_action(f"get_file_info:{workspace / 'unterordner'}")
    assert info.startswith("dir")
    execute_action("remove_directory:unterordner")
    assert not (workspace / "unterordner").exists()


def test_remove_directory_refuses_nonempty(workspace):
    execute_action("create_directory:voll")
    execute_action("write:voll/datei.txt|x")
    with pytest.raises(StepError, match="Nicht leer"):
        execute_action("remove_directory:voll")


def test_unknown_action(workspace):
    with pytest.raises(StepError, match="Unbekannte Aktion"):
        execute_action("teleport:nach_hause")
