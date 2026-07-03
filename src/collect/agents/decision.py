"""DecisionAgent — zerlegt Plan-Queries in konkrete, ausführbare Schritte.

Port der Decide-Rolle aus dem Alt-System: LLM-Decompose (synchroner Ollama-
Call im eigenen Bus-Thread), robuste JSON-Extraktion, Grammatik-Prüfung.
Nicht ausführbare Pläne (action="") werden als Inhalt präsentiert statt
ausgeführt (actionable=False).
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from collect.agents.base import BaseAgent
from collect.agents import ollama
from collect.bus import Message
from collect.config import settings

KNOWN_CODE_ACTIONS = {
    "list_files", "search_files", "get_file_info",
    "create_directory", "remove_directory",
}

ACTION_HINT = (
    '- "read:<pfad>" — Datei lesen\n'
    '- "write:<pfad>|<inhalt>" — Datei schreiben\n'
    '- "execute:<befehl>" — Skript/Befehl ausführen\n'
    '- "list_files:<verzeichnis>" — Verzeichnis auflisten\n'
    '- "search_files:<muster>" — Dateien suchen\n'
    '- "get_file_info:<pfad>" — Datei-Metadaten\n'
    '- "create_directory:<pfad>" / "remove_directory:<pfad>"'
)


def is_executable_action(action: str) -> bool:
    """Prüft die ausführbare Grammatik: read:/write:/execute: mit Argument,
    known-code-actions bare oder mit optionalem :arg (z.B. list_files:data/)."""
    if not action:
        return False
    head = action.split(":", 1)[0]
    if head in ("read", "write", "execute"):
        return ":" in action and len(action.split(":", 1)[1].strip()) > 0
    return head in KNOWN_CODE_ACTIONS


def parse_plan_steps(raw: str) -> list[dict]:
    """Extrahiert die Schritt-Liste aus der LLM-Antwort (robust gg. Geschwätz)."""
    if not raw:
        return []
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(raw[start:end + 1])
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    steps = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "") or "").strip()
        description = str(item.get("description", "") or "").strip()
        if not description and not action:
            continue
        steps.append({
            "id": item.get("id") or f"step_{i + 1}",
            "description": description or action,
            "action": action,
            "executable": is_executable_action(action),
        })
    return steps[:settings.max_plan_steps]


def decide_plan_steps(query: str, context: str = "",
                      generate_fn: Optional[Callable] = None) -> dict:
    """Reine Decide-Logik (transportfrei, testbar mit injiziertem generate_fn)."""
    generate = generate_fn or ollama.generate
    prompt = (
        "Du bist ein Planungs-Assistent. Zerlege die folgende Anfrage in eine "
        f"kurze, geordnete Liste von Schritten (max. {settings.max_plan_steps}).\n\n"
        "Für JEDEN Schritt, der eine konkrete Datei-/System-Operation ist, gib im "
        f'Feld "action" GENAU EINE der folgenden Formen an:\n{ACTION_HINT}\n\n'
        'Für rein gedankliche/informative Schritte setze "action": "".\n\n'
        "Antworte AUSSCHLIESSLICH mit einem JSON-Array, keine Erklärung. "
        'Format: [{"id":"step_1","description":"...","action":"..."}]\n\n'
        f"ANFRAGE: {query}"
    )
    if context:
        prompt += f"\n\nKONTEXT:\n{context[:1500]}"

    try:
        raw = generate(prompt, system="Antworte nur mit gültigem JSON-Array.",
                       timeout=settings.decide_timeout)
        steps = parse_plan_steps(raw)
    except Exception:
        steps = []

    if not steps:
        # Fallback: ein nicht-ausführbarer Schritt → Plan wird Inhalt.
        return {"status": "success", "actionable": False,
                "steps": [{"id": "step_1", "description": query,
                           "action": "", "executable": False}]}

    return {"status": "success",
            "actionable": any(s["executable"] for s in steps),
            "steps": steps}


class DecisionAgent(BaseAgent):
    name = "decision"

    def __init__(self, bus, generate_fn: Optional[Callable] = None):
        super().__init__(bus)
        self.generate_fn = generate_fn

    def subscriptions(self):
        return {"decision_request": self.on_request}

    def on_request(self, msg: Message) -> None:
        if msg.data.get("action") != "decide_plan_steps":
            return
        query = msg.data.get("query", "")
        result = decide_plan_steps(query, msg.data.get("context", ""),
                                   self.generate_fn)
        n_exec = sum(1 for s in result["steps"] if s["executable"])
        self.log.info("%s: %d Schritte (%d ausführbar)",
                      msg.correlation_id[:8], len(result["steps"]), n_exec)
        self.publish("decision_response", "decision_response",
                     result, msg.correlation_id)
