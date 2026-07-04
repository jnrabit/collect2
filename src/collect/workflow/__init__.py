"""Code-Workflow (Phase 6): Briefing → Planning → Execution → Verify → Commit.

Der Anti-Monolith-Schnitt: jede Phase ein kleines Modul mit einer Funktion
über dem gemeinsamen WorkflowContext; die Engine reiht sie auf. Jeder Write
läuft durch die Gates (Syntax + Regression-Guard + Security-Scan); Verify
führt pytest im begrenzten Workflow-Repo aus; committet wird nur bei Grün.
"""

from collect.workflow.context import WorkflowContext
from collect.workflow.engine import run_workflow

__all__ = ["WorkflowContext", "run_workflow"]
