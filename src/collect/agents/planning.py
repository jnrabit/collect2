"""PlanningAgent — Plan→Decide→Act-Kaskade als Zustandsmaschine.

Ablauf (kein Blocking-Wait, alles eventgetrieben):
  planning_request(create_and_execute)
    → decision_request(decide_plan_steps)          [Decide]
    → decision_response: Plan anlegen
        informativ → sofort planning_response (Plan als Inhalt)
        ausführbar → Schritte SEQUENZIELL via step_request  [Act]
    → step_response: Fortschritt (idempotent), nächster Schritt
    → alle fertig / Deadline → planning_response (einmalig, response_sent-Flag)

Die Doppelzählungs- und Timeout-Bugs des Alt-Systems sind hier per Design
adressiert: Schritt-Updates prüfen den Ist-Status, die finale Antwort ist
idempotent, Deadlines laufen über bus.call_later statt Ratelogik.
"""

from __future__ import annotations

import time

from collect.agents.base import BaseAgent
from collect.bus import Message, new_id
from collect.config import settings

TERMINAL = ("finished", "completed", "success", "failed", "error", "timeout")
OK_STATES = ("finished", "completed", "success")


class PlanningAgent(BaseAgent):
    name = "planning"

    def __init__(self, bus):
        super().__init__(bus)
        self._plans: dict[str, dict] = {}          # plan_id → Plan-Zustand
        self._decide_pending: dict[str, str] = {}  # decision-cid → plan_id

    def subscriptions(self):
        return {
            "planning_request": self.on_request,
            "decision_response": self.on_decision,
            "step_response": self.on_step_response,
        }

    # ── 1. Anfrage → Decide ──────────────────────────────────────────────

    def on_request(self, msg: Message) -> None:
        if msg.data.get("action") != "create_and_execute":
            return
        query = msg.data.get("query", "").strip()
        cid = msg.correlation_id
        plan_id = f"plan_{new_id()[:8]}"
        self._plans[plan_id] = {
            "query": query,
            "correlation_id": cid,
            "steps": [],
            "current": 0,
            "executed": False,
            "response_sent": False,
            "created_at": time.time(),
        }
        decide_cid = new_id()
        self._decide_pending[decide_cid] = plan_id

        self.progress(cid, "plan_decompose", "Zerlege Anfrage in Schritte…")
        self.publish("decision_request", "decision_request",
                     {"action": "decide_plan_steps", "query": query}, decide_cid)
        # Deadline für die Decide-Phase: bleibt die Antwort aus, wird der Plan
        # als leerer Plan finalisiert statt ewig zu hängen.
        self.bus.call_later(settings.decide_timeout + 10,
                            lambda: self._decide_deadline(decide_cid))

    def _decide_deadline(self, decide_cid: str) -> None:
        plan_id = self._decide_pending.pop(decide_cid, None)
        if plan_id is None:
            return  # Antwort kam rechtzeitig
        self.log.warning("Decide-Timeout für %s", plan_id)
        plan = self._plans.get(plan_id)
        if plan is not None:
            plan["message"] = "Konnte keinen Plan ableiten (Decide-Timeout)."
            self._finalize(plan_id, executed=False)

    # ── 2. Decide-Antwort → Plan anlegen, ggf. ausführen ────────────────

    def on_decision(self, msg: Message) -> None:
        plan_id = self._decide_pending.pop(msg.correlation_id, None)
        if plan_id is None:
            return
        plan = self._plans.get(plan_id)
        if plan is None:
            return

        steps = msg.data.get("steps", [])
        plan["steps"] = [{
            "id": s.get("id", f"step_{i + 1}"),
            "description": s.get("description", ""),
            "action": s.get("action", ""),
            "executable": bool(s.get("executable")),
            "status": "pending",
        } for i, s in enumerate(steps)]

        cid = plan["correlation_id"]
        if not msg.data.get("actionable") or not plan["steps"]:
            self.log.info("%s ist informativ (%d Schritte).", plan_id, len(plan["steps"]))
            self.progress(cid, "plan_ready", f"Plan erstellt ({len(plan['steps'])} Schritte)")
            self._finalize(plan_id, executed=False)
            return

        self.progress(cid, "plan_execute", f"Führe {len(plan['steps'])} Schritte aus…")
        self.log.info("Starte Ausführung von %s (%d Schritte)", plan_id, len(plan["steps"]))
        plan["executed"] = True
        self._run_next_step(plan_id)

    # ── 3. Sequenzielle Ausführung ───────────────────────────────────────

    def _run_next_step(self, plan_id: str) -> None:
        plan = self._plans.get(plan_id)
        if plan is None or plan["response_sent"]:
            return
        steps = plan["steps"]
        idx = plan["current"]

        # Nicht-ausführbare Schritte überspringen (als informativ markieren)
        while idx < len(steps) and not steps[idx]["executable"]:
            steps[idx]["status"] = "completed"
            steps[idx]["result"] = "(informativer Schritt)"
            idx += 1
        plan["current"] = idx

        if idx >= len(steps):
            self._finalize(plan_id, executed=True)
            return

        step = steps[idx]
        step["status"] = "running"
        self.publish("step_request", "step_request", {
            "plan_id": plan_id,
            "step_id": step["id"],
            "action": step["action"],
        }, plan["correlation_id"])
        self.bus.call_later(settings.step_timeout + 5,
                            lambda: self._step_deadline(plan_id, step["id"]))

    def _step_deadline(self, plan_id: str, step_id: str) -> None:
        plan = self._plans.get(plan_id)
        if plan is None or plan["response_sent"]:
            return
        step = self._find_step(plan, step_id)
        if step is None or step["status"] in TERMINAL:
            return  # Antwort kam rechtzeitig
        step["status"] = "timeout"
        step["error"] = f"Keine Antwort in {settings.step_timeout}s"
        self.log.warning("%s/%s: Schritt-Timeout", plan_id, step_id)
        plan["current"] += 1
        self._run_next_step(plan_id)

    def on_step_response(self, msg: Message) -> None:
        plan_id = msg.data.get("plan_id")
        plan = self._plans.get(plan_id)
        if plan is None or plan["response_sent"]:
            return
        step = self._find_step(plan, msg.data.get("step_id"))
        if step is None:
            return
        if step["status"] in TERMINAL:
            return  # Duplikat/Nachzügler — Fortschritt zählt nicht doppelt

        status = msg.data.get("status", "finished")
        step["status"] = status if status in TERMINAL else "finished"
        if msg.data.get("result"):
            step["result"] = str(msg.data["result"])[:300]
        if msg.data.get("error"):
            step["error"] = str(msg.data["error"])[:200]

        plan["current"] += 1
        self._run_next_step(plan_id)

    @staticmethod
    def _find_step(plan: dict, step_id) -> dict | None:
        for step in plan["steps"]:
            if step["id"] == step_id:
                return step
        return None

    # ── 4. Finale Antwort (idempotent) ───────────────────────────────────

    def _finalize(self, plan_id: str, executed: bool) -> None:
        plan = self._plans.get(plan_id)
        if plan is None or plan["response_sent"]:
            return
        plan["response_sent"] = True

        steps = plan["steps"]
        lines = []
        for i, step in enumerate(steps):
            desc = step["description"] or step["action"] or f"Schritt {i + 1}"
            if executed:
                mark = "✓" if step["status"] in OK_STATES else \
                       "✗" if step["status"] in ("failed", "error", "timeout") else "•"
                line = f"{mark} {desc}"
                if step.get("result"):
                    line += f"\n   → {step['result']}"
                if step.get("error"):
                    line += f"\n   ⚠ {step['error']}"
            else:
                line = f"{i + 1}. {desc}"
            lines.append(line)

        header = ("Ausführungsergebnis" if executed else "Plan") + f" für: {plan['query']}"
        body = plan.get("message") or (header + "\n\n" + "\n".join(lines))
        failed = sum(1 for s in steps if s["status"] in ("failed", "error", "timeout"))

        self.publish("planning_response", "planning_response", {
            "status": "completed" if failed == 0 else "completed_with_errors",
            "plan_id": plan_id,
            "executed": executed,
            "steps": steps,
            "message": body,
        }, plan["correlation_id"])
        self.log.info("%s finalisiert (executed=%s, %d Schritte, %d Fehler)",
                      plan_id, executed, len(steps), failed)
