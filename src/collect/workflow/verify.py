"""Verify — pytest im Workflow-Repo. Führt generierten Code aus, deshalb
strikt auf ctx.repo begrenzt (settings.workflow_repo liegt im Workspace)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from collect.config import settings


def has_tests(repo: Path) -> bool:
    return any(Path(repo).rglob("test_*.py")) or any(Path(repo).rglob("*_test.py"))


def run(ctx) -> None:
    repo = Path(ctx.repo)
    if not has_tests(repo):
        ctx.verify = {"ok": True, "skipped": True, "summary": "keine Tests"}
        return
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", str(repo)],
            capture_output=True, text=True, cwd=str(repo),
            timeout=settings.workflow_verify_timeout)
        tail = (r.stdout + r.stderr).strip()[-1200:]
        m = re.search(r"(\d+ passed[^\n]*|\d+ failed[^\n]*|no tests ran[^\n]*)",
                      tail.splitlines()[-1] if tail else "")
        ctx.verify = {
            "ok": r.returncode == 0,
            "skipped": False,
            "summary": m.group(1) if m else f"exit {r.returncode}",
            "output": tail,
        }
    except subprocess.TimeoutExpired:
        ctx.verify = {"ok": False, "skipped": False,
                      "summary": f"Timeout nach {settings.workflow_verify_timeout:.0f}s",
                      "output": ""}
