"""Sandbox — sichere Ausführung von generiertem Code im Subprozess.

Isolation: die process_group (os.setpgrp) macht den gesamten pytest-Baum mit
einem Signal killbar; Ressourcen-Limits (Wall-Time, Memory) verhindern
Endlos-Schleifen und RAM-Exhaustion; PYTHONPATH-Restriktion verhindert
versehentliche Importe außerhalb des Workflow-Repos.

Bewusst kein chroot/Container — das wäre Over-Engineering für lokal-first.
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def set_limits(timeout_s: float = 120.0, max_memory_mb: int = 1024) -> None:
    """Im Kind-Prozess: Ressourcen-Limits setzen. Best-effort (Linux)."""
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (int(timeout_s) + 5, int(timeout_s) + 10))
    except (ValueError, OSError):
        pass
    try:
        limit = max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError):
        pass


def _preexec(timeout_s: float, max_memory_mb: int):
    """Kind-Prozess-Setup: eigene Prozessgruppe (killbar) + Ressourcen-Limits.
    Als preexec_fn übergeben — läuft nach fork, vor exec."""
    def _setup():
        os.setpgrp()
        set_limits(timeout_s, max_memory_mb)
    return _setup


def sandbox_pytest(repo: Path, timeout: float = 120.0,
                   extra_args: Optional[list[str]] = None) -> dict:
    """Führt pytest im Kind-Prozess mit Isolation aus.
    → {ok, skipped, summary, output, sandboxed: bool}."""
    args = [sys.executable, "-m", "pytest", "-q", "--no-header"]
    if extra_args:
        args += extra_args
    args.append(str(repo))

    env = os.environ.copy()
    repo_str = str(repo.resolve())
    env["PYTHONPATH"] = repo_str
    env.pop("COLLECT_DATA_DIR", None)
    env.pop("COLLECT_API_TOKEN", None)

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=repo_str, env=env,
            preexec_fn=_preexec(timeout, 1024),
        )
        try:
            out_bytes, _ = proc.communicate(timeout=timeout)
            ret = proc.returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
            try:
                out_bytes, _ = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                out_bytes = b""
            ret = -9

        import re
        out = out_bytes.decode("utf-8", errors="replace")
        tail = out.strip()[-1200:]

        if ret == -9:
            summary = f"Timeout nach {timeout:.0f}s (Prozessgruppe gekillt)"
            return {"ok": False, "skipped": False, "summary": summary,
                    "output": tail, "sandboxed": True}

        m = re.search(r"(\d+ passed[^\n]*|\d+ failed[^\n]*|no tests ran[^\n]*)",
                      tail.splitlines()[-1] if tail else "")
        return {
            "ok": ret == 0,
            "skipped": False,
            "summary": m.group(1) if m else f"exit {ret}",
            "output": tail,
            "sandboxed": True,
        }
    except FileNotFoundError:
        return {"ok": False, "skipped": False,
                "summary": "pytest nicht gefunden", "output": "",
                "sandboxed": False}


def sandbox_run(script: str, repo: Path, timeout: float = 30.0) -> dict:
    """Führt ein Python-Skript isoliert aus (für execute:-Schritte).
    → {ok, stdout, stderr, sandboxed: bool}."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo.resolve())

    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False,
                                     dir=str(repo)) as f:
        f.write(script)
        tmp_path = f.name

    try:
        proc = subprocess.Popen(
            [sys.executable, tmp_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(repo), env=env,
            preexec_fn=_preexec(timeout, 1024),
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            return {
                "ok": proc.returncode == 0,
                "stdout": out.decode("utf-8", errors="replace")[:4000],
                "stderr": err.decode("utf-8", errors="replace")[:2000],
                "exit_code": proc.returncode,
                "sandboxed": True,
            }
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
            return {"ok": False, "stdout": "", "stderr": "Timeout",
                    "exit_code": -9, "sandboxed": True}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
