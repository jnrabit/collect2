"""WorkflowContext — der gemeinsame Zustand, den die Phasen anreichern."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkflowContext:
    task: str
    repo: Path
    briefing: str = ""
    plan: dict = field(default_factory=dict)      # {strategy, files:[{path, action, description}]}
    changes: list = field(default_factory=list)   # [{path, status, detail}]
    verify: dict = field(default_factory=dict)    # {ok, skipped, summary, output}
    commit: str = ""                              # Commit-Betreff oder ""
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and any(
            c.get("status") == "written" for c in self.changes)

    def report(self) -> str:
        """Menschlesbarer Abschlussbericht (landet in der Chat-Antwort)."""
        lines = [f"Code-Workflow für: {self.task}", ""]
        if self.plan.get("strategy"):
            lines += [f"**Strategie:** {self.plan['strategy']}", ""]
        for c in self.changes:
            mark = {"written": "✓", "blocked": "🛑", "failed": "✗"}.get(c["status"], "•")
            lines.append(f"{mark} {c['path']} — {c['detail']}")
        if self.verify:
            if self.verify.get("skipped"):
                lines.append("• Verify: übersprungen (keine Tests gefunden)")
            else:
                mark = "✓" if self.verify.get("ok") else "✗"
                lines.append(f"{mark} Verify: {self.verify.get('summary', '?')}")
        lines.append(f"✓ Commit: {self.commit}" if self.commit
                     else "• kein Commit (Verify nicht grün oder kein git-Repo)")
        for e in self.errors:
            lines.append(f"⚠ {e}")
        return "\n".join(lines)
