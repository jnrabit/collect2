"""WorkflowAgent — führt Code-Workflows aus (der 10. Agent).

Blockiert bewusst im eigenen Bus-Thread (ein Workflow dauert Minuten und
läuft exklusiv — parallele Workflows im selben Repo wären ein Datenrennen).
Progress-Events halten den Chat informiert.
"""

from __future__ import annotations

from typing import Callable, Optional

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.workflow.engine import run_workflow


class WorkflowAgent(BaseAgent):
    name = "workflow"

    def __init__(self, bus, generate_fn: Optional[Callable] = None):
        super().__init__(bus)
        self.generate_fn = generate_fn

    def subscriptions(self):
        return {"workflow_request": self.on_request}

    def on_request(self, msg: Message) -> None:
        task = (msg.data.get("task") or msg.data.get("query") or "").strip()
        cid = msg.correlation_id
        if not task:
            return
        self.log.info("%s: Workflow startet: %s…", cid[:8], task[:60])

        ctx = run_workflow(
            task,
            generate=self.generate_fn,
            progress=lambda stage, detail="": self.progress(cid, stage, detail),
        )

        self.publish("workflow_response", "workflow_response", {
            "report": ctx.report(),
            "ok": ctx.ok,
            "committed": bool(ctx.commit),
            "changes": ctx.changes,
            "verify": ctx.verify,
        }, cid)
        self.log.info("%s: Workflow fertig (ok=%s, commit=%s)",
                      cid[:8], ctx.ok, bool(ctx.commit))
