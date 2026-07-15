"""Execution — pro geplanter Datei Code generieren, durch die Gates, schreiben.

Gates vor JEDEM Write (die deterministische Schiene aus vibelike, hier als
Pflicht statt Option): Syntax (.py), Regression-Guard (Symbol-Verlust nur
wenn der Plan es autorisiert), Security-Scan (high blockt).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from collect import prompts
from collect.regression_guard import check_change
from collect.validation import write_blockers
from collect.workflow.idioms import inject_idiom

# Texte zentral in collect.prompts (extern überschreibbar via
# COLLECT_PROMPTS_DIR/workflow_codegen.txt bzw. workflow_repair.txt);
# Aliase für bestehende Importe.
PROMPT = prompts.embedded("workflow_codegen")

_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)

REPAIR_PROMPT = prompts.embedded("workflow_repair")

_REPAIR_PATH = re.compile(r"PFAD:\s*([^\s`]+)")


def extract_code(raw: str) -> str:
    """Inhalt des ersten Code-Fences; ohne Fence: Rohtext (getrimmt)."""
    if not raw:
        return ""
    m = _FENCE.search(raw)
    return (m.group(1) if m else raw).strip() + "\n"


def gate(path: str, old: str, new: str, plan_text: str) -> list[str]:
    """→ Liste der Blocker (leer = Write erlaubt)."""
    blockers = []
    if path.endswith(".py"):
        try:
            ast.parse(new)
        except SyntaxError as e:
            blockers.append(f"Syntax: {e}")
    if old:
        for issue in check_change(path, old, new, plan_text=plan_text):
            if issue["kind"] == "symbol_loss":
                blockers.append(f"Regression-Guard: {issue['detail']}")
    blockers += [f"Security: {b}" for b in write_blockers(new, path)]
    return blockers


def execute(ctx, generate, progress=None) -> None:
    repo = Path(ctx.repo)
    plan_text = ctx.plan.get("strategy", "") + " " + " ".join(
        f["description"] for f in ctx.plan.get("files", []))
    # Bereits geschriebene Dateien in Folge-Prompts zeigen — sonst
    # halluziniert die Testdatei Erwartungswerte, die die Implementierung
    # nie liefert (real beobachtet mit qwen2.5:7b).
    written_parts: list[str] = []

    for spec in ctx.plan.get("files", []):
        rel = spec["path"]
        target = repo / rel
        old = ""
        if target.exists():
            old = target.read_text(encoding="utf-8", errors="replace")
        current = (f"AKTUELLER INHALT:\n```python\n{old[:4000]}\n```\n"
                   if old else "(Datei ist neu)\n")
        written = ("\nBEREITS GESCHRIEBEN IN DIESEM WORKFLOW:\n"
                   + "\n".join(written_parts) + "\n") if written_parts else ""

        if progress:
            progress("workflow_execute", f"{rel} wird generiert…")
        try:
            raw = generate(inject_idiom(prompts.get_prompt("workflow_codegen").format(
                task=ctx.task, strategy=ctx.plan.get("strategy", ""),
                path=rel, description=spec["description"], current=current,
                written=written), ctx.task, "execution"))
            if isinstance(raw, tuple):
                raw = raw[0]
            content = extract_code(raw)
        except Exception as e:
            ctx.changes.append({"path": rel, "status": "failed",
                                "detail": f"LLM-Fehler: {e}"})
            continue

        if not content.strip():
            ctx.changes.append({"path": rel, "status": "failed",
                                "detail": "leere Generierung"})
            continue

        blockers = gate(rel, old, content, plan_text)
        if blockers:
            ctx.changes.append({"path": rel, "status": "blocked",
                                "detail": "; ".join(blockers)[:300]})
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written_parts.append(f"--- {rel} ---\n{content[:2500]}")
        ctx.changes.append({"path": rel, "status": "written",
                            "detail": f"{len(content)} Zeichen "
                                      f"({'neu' if not old else 'geändert'})"})


def repair(ctx, generate, progress=None) -> bool:
    """Eine Reparatur-Runde: Verify-Fehler + Datei-Inhalte ans LLM, die
    benannte Datei durch die Gates neu schreiben. → True wenn etwas repariert."""
    repo = Path(ctx.repo)
    written = [c["path"] for c in ctx.changes if c["status"] in ("written", "repaired")]
    if not written:
        return False

    def _safe_read(rel: str) -> str:
        # Datei könnte zwischen Write und Repair verschwinden → nicht crashen
        try:
            return (repo / rel).read_text(encoding="utf-8", errors="replace")[:2500]
        except OSError as e:
            return f"(nicht lesbar: {e})"

    files_block = "\n\n".join(f"--- {p} ---\n{_safe_read(p)}" for p in written)
    try:
        raw = generate(prompts.get_prompt("workflow_repair").format(
            task=ctx.task, failure=ctx.verify.get("output", "")[:1500],
            files=files_block))
        if isinstance(raw, tuple):
            raw = raw[0]
    except Exception as e:
        ctx.errors.append(f"Reparatur-LLM-Fehler: {e}")
        return False

    m = _REPAIR_PATH.search(raw or "")
    content = extract_code(raw)
    if not m or not content.strip():
        ctx.errors.append("Reparatur: Antwort ohne PFAD/Code.")
        return False
    rel = m.group(1).strip()
    if rel not in written:
        ctx.errors.append(f"Reparatur: {rel} ist keine geschriebene Datei.")
        return False

    old = (repo / rel).read_text(encoding="utf-8", errors="replace")
    plan_text = ctx.plan.get("strategy", "")
    blockers = gate(rel, old, content, plan_text)
    if blockers:
        ctx.changes.append({"path": rel, "status": "blocked",
                            "detail": "Reparatur geblockt: " + "; ".join(blockers)[:250]})
        return False

    (repo / rel).write_text(content, encoding="utf-8")
    ctx.changes.append({"path": rel, "status": "repaired",
                        "detail": "nach Verify-Fehler korrigiert"})
    if progress:
        progress("workflow_repaired", rel)
    return True
