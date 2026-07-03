"""LLMAgent — geerdete Antwort-Synthese (Ollama, lokal).

Zustandsmaschine statt Blocking-Wait: llm_request registriert eine pendende
Anfrage mit `needs` (welche Retrieval-Beiträge kommen); die *_response-Events
füllen sie auf. Sind alle Beiträge da, wird generiert. Liegen ALLE Zonen im
Hard-Fallback, wird das Generieren übersprungen (spart 10–30s Ollama-Zeit;
der ResponseAgent unterdrückt die Antwort ohnehin).
"""

from __future__ import annotations

from typing import Callable, Optional

from collect.agents.base import BaseAgent
from collect.agents import ollama
from collect.bus import Message
from collect.config import settings
from collect.retrieval.zones import ZONE_FALLBACK

TOP_DOCS = 4          # wie vibelike: Top-4-Quellen in den System-Prompt
DOC_CHARS = 450

SYSTEM_PROMPT = (
    "Du bist ein Wissensassistent. Beantworte die Frage des Nutzers präzise "
    "und auf Deutsch, GESTÜTZT auf die bereitgestellten Quellen. Wenn die "
    "Quellen die Frage nicht abdecken, sage das ehrlich. Erfinde keine Fakten."
)


class LLMAgent(BaseAgent):
    name = "llm"

    def __init__(self, bus, generate_fn: Optional[Callable] = None, grounder=None):
        """grounder: optionaler FactGrounder — verbürgte Ossifikat-Fakten
        werden dem Prompt autoritativ vorangestellt."""
        super().__init__(bus)
        self.generate = generate_fn or ollama.generate
        self.grounder = grounder
        self._pending: dict[str, dict] = {}  # cid → {query, needs, contribs}
        # Beiträge, die VOR dem llm_request eintreffen (Race: Retrieval kann
        # schneller sein als die Zustellung des Requests) — begrenzt gepuffert.
        self._early: dict[str, dict] = {}

    def subscriptions(self):
        return {
            "llm_request": self.on_request,
            "retrieval_response": self.on_contribution("retrieval"),
            "code_retrieval_response": self.on_contribution("code_retrieval"),
        }

    def on_request(self, msg: Message) -> None:
        cid = msg.correlation_id
        self._pending[cid] = {
            "query": msg.data.get("original_query") or msg.data.get("query", ""),
            "effective": msg.data.get("query", ""),
            "needs": set(msg.data.get("needs") or ["retrieval"]),
            "contribs": self._early.pop(cid, {}),
        }
        self._maybe_generate(cid)

    def on_contribution(self, kind: str):
        def handler(msg: Message) -> None:
            cid = msg.correlation_id
            state = self._pending.get(cid)
            if state is None:
                # Request (noch) nicht da — puffern statt verlieren
                self._early.setdefault(cid, {})[kind] = msg.data
                while len(self._early) > 256:
                    self._early.pop(next(iter(self._early)))
                return
            state["contribs"][kind] = msg.data
            self._maybe_generate(cid)
        return handler

    def _maybe_generate(self, cid: str) -> None:
        state = self._pending.get(cid)
        if state is None or not state["needs"] <= set(state["contribs"]):
            return
        del self._pending[cid]

        # Verbürgte Fakten (Ossifikat) — autoritatives Grounding
        facts = []
        if self.grounder:
            try:
                facts = self.grounder.relevant_facts(state["effective"] or state["query"])
            except Exception as e:
                self.log.warning("%s: Fakt-Grounding fehlgeschlagen: %s", cid[:8], e)

        zones = [c.get("zone") for c in state["contribs"].values()]
        if zones and all(z == ZONE_FALLBACK for z in zones) and not facts:
            self.log.info("%s: alle Zonen FALLBACK, keine Fakten — LLM übersprungen", cid[:8])
            self.publish("llm_response", "llm_response",
                         {"content": "", "skipped": True, "model": "", "facts_used": 0}, cid)
            return

        self.progress(cid, "llm_generating", "Antwort wird generiert…")
        prompt = self._build_prompt(state, facts)
        try:
            content = self.generate(prompt, system=SYSTEM_PROMPT)
            self.publish("llm_response", "llm_response", {
                "content": content.strip(), "skipped": False,
                "model": settings.main_model,
                "facts_used": len(facts),
            }, cid)
            self.log.info("%s: Antwort generiert (%d Zeichen, %d Fakten)",
                          cid[:8], len(content), len(facts))
        except Exception as e:
            self.log.error("%s: LLM-Fehler: %s", cid[:8], e)
            self.publish("llm_response", "llm_response",
                         {"content": "", "skipped": False, "error": str(e),
                          "facts_used": len(facts)}, cid)

    def _build_prompt(self, state: dict, facts: list | None = None) -> str:
        docs = []
        for kind in ("retrieval", "code_retrieval"):
            contrib = state["contribs"].get(kind)
            if not contrib or contrib.get("zone") == ZONE_FALLBACK:
                continue  # entfernte Treffer erden, Fallback-Treffer nicht
            docs.extend(contrib.get("hits", []))
        docs.sort(key=lambda h: h.get("distance", 999.0))

        # Verbürgte Fakten VOR den Quellen — bei Widerspruch haben sie Vorrang
        fact_block = ""
        if facts:
            fl = "\n".join(f"- {f['content']}" for f in facts)
            fact_block = ("VERBÜRGTE FAKTEN (vom Nutzer bestätigt — als gesichert "
                          "behandeln, bei Widerspruch haben sie Vorrang vor den "
                          f"QUELLEN):\n{fl}\n\n")

        parts = []
        for i, doc in enumerate(docs[:TOP_DOCS], 1):
            title = doc.get("title") or doc.get("doc_id", f"Quelle {i}")
            parts.append(f"[{i}] {title}:\n{doc.get('content', '')[:DOC_CHARS]}")

        context = "\n\n".join(parts) if parts else "(keine Quellen verfügbar)"
        return (f"{fact_block}QUELLEN:\n{context}\n\n"
                f"FRAGE: {state['query']}\n\n"
                f"Antworte gestützt auf die verbürgten Fakten und Quellen.")
