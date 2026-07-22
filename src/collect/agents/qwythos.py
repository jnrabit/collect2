"""QwythosAgent — Modell-getriebener Agent (Tool-Calling statt Pipeline).

Aktiv wenn `COLLECT_QWYTHOS_ENABLED=true` und das Modell geladen ist.
Ersetzt den Orchestrator: die Query geht direkt ans Modell, das via
Tool-Calls (retrieve, translate, decompose, rewrite, classify, answer, done)
die Pipeline selbst steuert. Jeder Schritt wird als Trace aufgezeichnet.

Implementiert als BaseAgent, registriert sich auf `user_query` mit höherer
Priorität als der Orchestrator. Wenn aktiv, wird der Orchestrator nie
aufgerufen (der QwythosAgent publiziert `user_response` direkt).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.config import settings
from collect.qwythos.tools import TOOL_DEFINITIONS, SYSTEM_PROMPT, call_tool

logger = logging.getLogger("agent.qwythos")

MAX_ROUNDS = 10


class QwythosAgent(BaseAgent):
    name = "qwythos"

    def __init__(self, bus, generate_fn=None):
        super().__init__(bus)
        if generate_fn is None:
            from collect.agents import ollama
            def _gen(prompt, **kw):
                return ollama.generate(prompt, model=settings.main_model,
                                       timeout=settings.llm_timeout, **kw)
            generate_fn = _gen
        self.generate = generate_fn
        self.tools = TOOL_DEFINITIONS
        self._active: dict[str, dict] = {}

    def subscriptions(self):
        return {"user_query": self.on_user_query}

    def on_user_query(self, msg: Message) -> None:
        if not settings.qwythos_enabled:
            return

        query = (msg.data.get("query") or "").strip()
        cid = msg.correlation_id
        if not query:
            return

        self._active[cid] = {
            "query": query,
            "history": msg.data.get("history") or [],
            "steps": [],
        }
        self.log.info("Qwythos %s: %s…", cid[:8], query[:60])
        self.progress(cid, "qwythos_start", "Modell steuert…")

        try:
            result = self._run_loop(cid, query, msg)
        except Exception as e:
            self.log.error("Qwythos-Fehler: %s", e)
            result = {"text": f"⚠️ Qwythos-Fehler: {e}", "meta": {"error": str(e)}}

        self.publish("user_response", "user_response", {
            "text": result.get("text", ""),
            "meta": result.get("meta", {}),
            "qwythos": True,
        }, cid)

    def _run_loop(self, cid: str, query: str, msg: Message) -> dict:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
        history = msg.data.get("history") or []
        if history:
            for turn in history[-2:]:
                messages.append({"role": "user", "content": str(turn.get("q", ""))[:300]})
                messages.append({"role": "assistant", "content": str(turn.get("a", ""))[:500]})

        context_parts: list[str] = []

        for round_num in range(MAX_ROUNDS):
            self.progress(cid, "qwythos_round", f"Runde {round_num + 1}")

            response = self._call_model(messages)
            if response is None:
                return {"text": "⚠️ Modell antwortete nicht.", "meta": {"rounds": round_num}}

            tool_calls = response.get("tool_calls") or []
            content = response.get("content", "") or ""

            if not tool_calls and content:
                return {
                    "text": content.strip(),
                    "meta": {
                        "rounds": round_num + 1,
                        "steps": self._active[cid]["steps"],
                    },
                }

            if not tool_calls:
                self.log.warning("Qwythos: leere Antwort in Runde %d", round_num)
                return {"text": "⚠️ Modell produzierte keinen Inhalt.", "meta": {}}

            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    args = {}

                self._active[cid]["steps"].append(name)

                if name == "done":
                    return {
                        "text": content.strip() or "Fertig.",
                        "meta": {"rounds": round_num + 1,
                                 "steps": self._active[cid]["steps"]},
                    }

                elif name == "answer":
                    c = args.get("context", "\n".join(context_parts))
                    return {
                        "text": content.strip() or self._generate_answer(query, c),
                        "meta": {"rounds": round_num + 1,
                                 "steps": self._active[cid]["steps"]},
                    }

                else:
                    result = call_tool(name, args)
                    if name == "retrieve":
                        try:
                            r = json.loads(result)
                            for h in r.get("hits", []):
                                context_parts.append(f"[{h.get('title', '?')}]: {h.get('content', '')[:800]}")
                        except json.JSONDecodeError:
                            pass

                    messages.append({
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": [tc],
                    })
                    messages.append({
                        "role": "tool",
                        "content": result,
                        "name": name,
                    })

        return {"text": f"⚠️ Maximale Runden ({MAX_ROUNDS}) erreicht.",
                "meta": {"rounds": MAX_ROUNDS, "steps": self._active[cid]["steps"]}}

    def _call_model(self, messages: list[dict]) -> Optional[dict]:
        """Ruft das Ollama-Modell mit Tool-Definitionen auf.
        Parst die Antwort auf tool_calls + content."""
        payload = {
            "model": settings.main_model,
            "messages": messages,
            "stream": False,
            "tools": self.tools,
            "options": {"temperature": 0.1, "num_predict": 2048},
        }
        import requests
        try:
            resp = requests.post(
                f"{settings.ollama_url}/api/chat",
                json=payload, timeout=settings.llm_timeout)
            resp.raise_for_status()
            data = resp.json()
            message = data.get("message", {})
            return {
                "content": message.get("content", ""),
                "tool_calls": message.get("tool_calls", []),
            }
        except Exception as e:
            self.log.error("Qwythos-Modell-Call fehlgeschlagen: %s", e)
            return None

    def _generate_answer(self, query: str, context: str) -> str:
        """Fallback: generiere Antwort ohne Tool-Call wenn answer-Tool
        kein content mitgab."""
        try:
            prompt = (
                f"QUELLEN:\n{context[:6000]}\n\n"
                f"FRAGE: {query}\n\n"
                f"Antworte gestützt auf die Quellen auf Deutsch."
            )
            return self.generate(prompt[:8000], system=SYSTEM_PROMPT)
        except Exception:
            return "⚠️ Konnte keine Antwort generieren."
