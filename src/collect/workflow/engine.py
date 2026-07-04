"""Engine — reiht die Phasen auf: Briefing → Planning → Execution → Verify → Commit.

Commit nur bei grünem Verify und wenn ctx.repo ein git-Repo ist; die
Commit-Message trägt den Task. Fehler brechen früh und landen im Report.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from collect.config import settings
from collect.workflow import briefing as briefing_phase
from collect.workflow import execution, planning, verify
from collect.workflow.context import WorkflowContext


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=30)


def _commit(ctx: WorkflowContext) -> None:
    repo = Path(ctx.repo)
    if not (repo / ".git").exists():
        return
    _git(repo, "add", "-A")
    subject = f"workflow: {ctx.task[:64]}"
    r = _git(repo, "commit", "-m",
             f"{subject}\n\nAutomatisch generiert (collect2 Code-Workflow).\n"
             f"Verify: {ctx.verify.get('summary', '?')}")
    if r.returncode == 0:
        ctx.commit = subject
    elif "nothing to commit" not in r.stdout + r.stderr:
        ctx.errors.append(f"Commit fehlgeschlagen: {(r.stderr or r.stdout)[:200]}")


def run_workflow(task: str, repo=None, generate=None,
                 progress=None, code_hits: list | None = None) -> WorkflowContext:
    """progress: optional (stage, detail) → None; generate: LLM-Funktion."""
    if generate is None:
        from collect.agents import ollama

        def generate(prompt, system="", **kw):
            # Code-Workflows nutzen das CODE-Modell (settings.code_model) —
            # der Generalist scheiterte real an exakten Test-Erwartungen.
            return ollama.generate(prompt, system=system,
                                   model=settings.code_model)

    repo = Path(repo or settings.workflow_repo)
    repo.mkdir(parents=True, exist_ok=True)
    gitignore = repo / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("__pycache__/\n*.pyc\n.pytest_cache/\n")
    ctx = WorkflowContext(task=task, repo=repo)

    def note(stage: str, detail: str = ""):
        if progress:
            progress(stage, detail)

    note("workflow_briefing", "Kontext wird gesammelt…")
    ctx.briefing = briefing_phase.gather(task, repo, code_hits)

    note("workflow_planning", "Datei-Plan wird erstellt…")
    ctx.plan = planning.plan(task, ctx.briefing, generate)
    if not ctx.plan.get("files"):
        ctx.errors.append("Planning lieferte keine gültigen Datei-Änderungen.")
        return ctx
    note("workflow_planned",
         f"{len(ctx.plan['files'])} Datei(en): "
         + ", ".join(f["path"] for f in ctx.plan["files"]))

    execution.execute(ctx, generate, progress=progress)
    if not any(c["status"] == "written" for c in ctx.changes):
        ctx.errors.append("Kein Write hat die Gates passiert — Abbruch vor Verify.")
        return ctx

    note("workflow_verify", "Tests laufen…")
    verify.run(ctx)

    # Reparatur-Schleife: roter Verify geht zurück ans LLM (max. N Runden)
    rounds = 0
    while (not ctx.verify.get("ok") and not ctx.verify.get("skipped")
           and rounds < settings.workflow_repair_rounds):
        rounds += 1
        note("workflow_repair", f"Verify rot — Reparatur-Runde {rounds}…")
        if not execution.repair(ctx, generate, progress=progress):
            break
        verify.run(ctx)

    if ctx.verify.get("ok"):
        _commit(ctx)
    return ctx
