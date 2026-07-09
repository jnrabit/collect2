"""Verify — pytest im Workflow-Repo. Führt generierten Code aus, deshalb
strikt auf ctx.repo begrenzt (settings.workflow_repo liegt im Workspace).

Phase 7: Sandbox-Isolation (Prozessgruppe, PYTHONPATH-Restriktion, Timeout-Kill)."""

from __future__ import annotations

from pathlib import Path

from collect.workflow.sandbox import sandbox_pytest


def has_tests(repo: Path) -> bool:
    return any(Path(repo).rglob("test_*.py")) or any(Path(repo).rglob("*_test.py"))


def run(ctx) -> None:
    repo = Path(ctx.repo)
    if not has_tests(repo):
        ctx.verify = {"ok": True, "skipped": True, "summary": "keine Tests"}
        return
    ctx.verify = sandbox_pytest(repo)
