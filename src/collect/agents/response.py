"""ResponseAgent — sammelt Beiträge laut Manifest und synthetisiert die Antwort.

Finalisierung ist EXPLIZIT (DESIGN.md §5.5): das Manifest des Orchestrators
sagt, welche Beiträge kommen; finalisiert wird bei Vollständigkeit oder
Deadline (bus.call_later) — die Timeout-Heuristiken des Alt-Systems entfallen.

Synthese (Reihenfolge aus der Stabilisierung des Alt-Systems):
  1. Plan-Sektion zuerst (falls planning-Beitrag)
  2. LLM-Antwort — mit Grauzonen-Hinweis, oder Hard-Fallback-Text wenn die
     Zone FALLBACK ist (Halluzinations-Schutz; Plan-Antworten werden nie
     unterdrückt)
  3. Quellen-Fußzeile (Trefferzahl, beste Distance, Zone)
"""

from __future__ import annotations

import time

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.retrieval.zones import (
    NO_HIT_DISTANCE,
    ZONE_FALLBACK,
    ZONE_GRAY,
    classify_zone,
)

CONTRIB_CHANNELS = {
    "retrieval_response": "retrieval",
    "code_retrieval_response": "code_retrieval",
    "llm_response": "llm",
    "planning_response": "planning",
    "workflow_response": "workflow",
}


class ResponseAgent(BaseAgent):
    name = "response"

    def __init__(self, bus):
        super().__init__(bus)
        self._states: dict[str, dict] = {}  # cid → {expected, contribs, …}

    def subscriptions(self):
        subs = {"response_manifest": self.on_manifest}
        for channel in CONTRIB_CHANNELS:
            subs[channel] = self.on_contribution
        return subs

    # ── Manifest + Sammeln ───────────────────────────────────────────────

    def on_manifest(self, msg: Message) -> None:
        cid = msg.correlation_id
        deadline = float(msg.data.get("deadline", 120.0))
        self._states[cid] = {
            "query": msg.data.get("query", ""),
            "rewritten_query": msg.data.get("rewritten_query"),
            "expected": set(msg.data.get("expected", [])),
            "contribs": {},
            "reply_to": msg.reply_to or "user_response",
            "finalized": False,
            "started_at": time.time(),
        }
        self.log.info("%s: Manifest %s (Deadline %.0fs)",
                      cid[:8], sorted(self._states[cid]["expected"]), deadline)
        self.bus.call_later(deadline, lambda: self._deadline(cid))

    def on_contribution(self, msg: Message) -> None:
        state = self._states.get(msg.correlation_id)
        if state is None or state["finalized"]:
            return
        kind = CONTRIB_CHANNELS.get(msg.type)
        if kind is None or kind in state["contribs"]:
            return  # unbekannt oder Duplikat
        state["contribs"][kind] = msg.data
        have = len(set(state["contribs"]) & state["expected"])
        self.log.info("%s: Beitrag %s (%d/%d)", msg.correlation_id[:8], kind,
                      have, len(state["expected"]))
        if state["expected"] <= set(state["contribs"]):
            self._finalize(msg.correlation_id, reason="complete")

    def _deadline(self, cid: str) -> None:
        state = self._states.get(cid)
        if state is None or state["finalized"]:
            return
        missing = state["expected"] - set(state["contribs"])
        self.log.warning("%s: Deadline — fehlend: %s", cid[:8], sorted(missing))
        self._finalize(cid, reason="deadline")

    # ── Synthese ─────────────────────────────────────────────────────────

    def _finalize(self, cid: str, reason: str) -> None:
        state = self._states.pop(cid, None)
        if state is None or state["finalized"]:
            return
        state["finalized"] = True

        text, meta = synthesize(state)
        meta["finalize_reason"] = reason
        meta["duration_s"] = round(time.time() - state["started_at"], 2)

        self.publish(state["reply_to"], "user_response",
                     {"text": text, "meta": meta}, cid)

        # Lern-Event für den LearningAgent (Triplet-Log + Fakt-Staging) —
        # asynchron zum Antwort-Pfad, der Nutzer wartet nie aufs Lernen.
        context_ids = []
        for kind in ("retrieval", "code_retrieval"):
            contrib = state["contribs"].get(kind) or {}
            context_ids.extend(h.get("doc_id") for h in contrib.get("hits", []))
        self.publish("answer_recorded", "answer_recorded", {
            "query": state["query"],
            "text": text,
            "zone": meta.get("zone"),
            "best_distance": meta.get("best_distance"),
            "plan_id": meta.get("plan_id"),
            "context_ids": context_ids,
        }, cid)
        self.log.info("%s: finalisiert (%s, %.1fs)", cid[:8], reason, meta["duration_s"])


def synthesize(state: dict) -> tuple[str, dict]:
    """Reine Synthese-Funktion (transportfrei, direkt testbar)."""
    contribs = state["contribs"]
    expected = state["expected"]
    parts: list[str] = []
    meta: dict = {}
    if state.get("rewritten_query"):
        meta["rewritten_query"] = state["rewritten_query"]

    # 1. Plan-/Workflow-Sektion zuerst — werden nie unterdrückt
    planning = contribs.get("planning")
    if planning:
        parts.append(planning.get("message", ""))
        meta["plan_id"] = planning.get("plan_id")
        meta["executed"] = planning.get("executed")
    workflow = contribs.get("workflow")
    if workflow:
        parts.append(workflow.get("report", ""))
        meta["workflow_ok"] = workflow.get("ok")
        meta["committed"] = workflow.get("committed")

    # Zonen-Lage über die Retrieval-Beiträge
    retrieval = contribs.get("retrieval")
    code = contribs.get("code_retrieval")
    best = min((c.get("best_distance", NO_HIT_DISTANCE)
                for c in (retrieval, code) if c), default=NO_HIT_DISTANCE)
    verdict = classify_zone(best if best < NO_HIT_DISTANCE else None)
    meta["zone"] = verdict.zone
    meta["best_distance"] = verdict.best_distance

    # 2. LLM-Antwort mit Drei-Zonen-Logik. Verbürgte Fakten heben den
    # Hard-Fallback auf: eine von Fakten geerdete Antwort wird nicht unterdrückt.
    llm = contribs.get("llm")
    facts_used = int(llm.get("facts_used", 0)) if llm else 0
    meta["facts_used"] = facts_used
    if llm and llm.get("eval_count"):
        meta["tokens"] = llm["eval_count"]
        meta["tok_per_s"] = llm.get("tok_per_s")
    if llm is not None:
        content = (llm.get("content") or "").strip()
        if (verdict.zone == ZONE_FALLBACK and not planning and not workflow
                and not (facts_used and content)):
            parts.append(
                "⚠️ Diese Frage liegt außerhalb des indizierten Wissensbereichs. "
                "Der Vault enthält keine ausreichend nahen Dokumente "
                f"(beste Distance: {verdict.best_distance:.1f}, "
                f"Schwellwert: {verdict.soft_max_distance:.0f}).")
        elif content:
            if verdict.zone == ZONE_GRAY:
                parts.append(
                    f"ℹ️ *Nur entfernte Vault-Treffer (beste Distance "
                    f"{verdict.best_distance:.1f} > {verdict.trust_threshold:.0f}) "
                    "— Antwort mit Vorsicht genießen.*")
            parts.append(content)
        elif llm.get("error"):
            parts.append(f"⚠️ LLM-Fehler: {llm['error']}")
    elif "llm" in expected and not planning:
        parts.append("⚠️ Keine LLM-Antwort erhalten (Timeout).")

    # 3. Quellen-Fußzeile
    footer = []
    if facts_used:
        footer.append(f"🔖 {facts_used} verbürgte(r) Fakt(en) als Grounding")
    if retrieval and retrieval.get("count"):
        footer.append(f"📚 General-Vault: {retrieval['count']} Treffer "
                      f"(beste Distance {retrieval.get('best_distance', 0):.1f})")
    if code and code.get("count"):
        footer.append(f"💻 Code-Vault: {code['count']} Treffer "
                      f"(beste Distance {code.get('best_distance', 0):.1f})")
    missing = expected - set(contribs)
    if missing:
        footer.append(f"⚠️ Ausstehend geblieben: {', '.join(sorted(missing))}")
    if footer:
        parts.append("\n".join(footer))

    text = "\n\n".join(p for p in parts if p) or "⚠️ Keine Antwort verfügbar."
    return text, meta
