"""MeetingProtokoll — Auto-Summarizer für Session-Kontext.

Der Name aus vibelike übernommen. Verdichtet Session-Kontext per kleinem
LLM zu 3-5 Kernfakten, wenn die Session N Turns erreicht. Asynchron zum
Antwort-Pfad — der Nutzer wartet nie auf die Summarization.
"""

from __future__ import annotations

import logging
from typing import Optional, Callable

from collect.agents import ollama
from collect.config import settings
from collect.session import SessionStore

logger = logging.getLogger("agent.meeting")


SUMMARY_PROMPT = """Du bist ein präziser Zusammenfasser. Verdichte den folgenden Gesprächsverlauf
auf 3-5 KERNSÄTZE. Nur Fakten und Ergebnisse, keine Höflichkeitsfloskeln.
Schreibe auf Deutsch, maximal 300 Zeichen.

GESPRÄCH:
{turns}

ZUSAMMENFASSUNG:"""


class MeetingProtokoll:
    def __init__(self, store: Optional[SessionStore] = None,
                 generate_fn: Optional[Callable] = None):
        self.store = store or SessionStore()
        if generate_fn is None:
            generate_fn = ollama.generate
        self.generate = generate_fn
        self._model = settings.session_summary_model or settings.decompose_model

    def summarize_if_needed(self, session_id: str) -> Optional[str]:
        if not self.store.needs_summary(session_id):
            return None
        return self.summarize(session_id)

    def summarize(self, session_id: str) -> Optional[str]:
        turns = self.store.get_turns(session_id, limit=12)
        if not turns:
            return None

        pairs = []
        for t in turns:
            pairs.append(f"Q: {str(t.get('q', ''))[:200]}")
            pairs.append(f"A: {str(t.get('a', ''))[:300]}")

        prompt = SUMMARY_PROMPT.format(turns="\n".join(pairs))

        try:
            raw = self.generate(prompt, model=self._model,
                                timeout=30, temperature=0.1)
            if isinstance(raw, tuple):
                raw = raw[0]
            summary = raw.strip()[:500]
            if summary:
                self.store.set_summary(session_id, summary,
                                       len(turns))
                logger.info("Session %s: Summarized %d turns",
                            session_id[:8], len(turns))
                return summary
        except Exception as e:
            logger.warning("Summary fehlgeschlagen: %s", e)
        return None
