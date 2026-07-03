"""LearningAgent — schließt die Grounding-Schleife.

Hört auf answer_recorded (vom ResponseAgent nach jeder Finalisierung):
  1. Triplet-Log: (Query, Kontext-IDs, Antwort) → JSONL, immer.
  2. Fakt-Extraktion: NUR TRUST-Antworten werden per LLM in Tripel zerlegt
     und ins Ossifikat-STAGING gelegt (Grauzone/Fallback ossifizieren nicht —
     kein Halluzinations-Substrat). Verbürgt werden sie erst durch menschliche
     Bestätigung (ossifikat-CLI) — das Staging-Gate ist die Determinismus-
     Schiene aus vibelike.

Beides best-effort und asynchron zum Antwort-Pfad: der Nutzer wartet nie
auf das Lernen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.config import settings
from collect.grounding.triplets import log_triplet
from collect.retrieval.zones import ZONE_TRUST


class LearningAgent(BaseAgent):
    name = "learning"

    def __init__(self, bus, extractor=None):
        """extractor: Objekt mit extract_and_stage(text, store, source) —
        default ossifikat.QwenExtractor (lazy, injizierbar für Tests)."""
        super().__init__(bus)
        self._extractor = extractor
        self.staged_total = 0

    def subscriptions(self):
        return {"answer_recorded": self.on_answer}

    def on_answer(self, msg: Message) -> None:
        d = msg.data
        query, text = d.get("query", ""), d.get("text", "")
        if not query or not text:
            return

        triplet_id = log_triplet(query, text, d.get("context_ids", []), meta={
            "zone": d.get("zone"),
            "best_distance": d.get("best_distance"),
            "correlation_id": msg.correlation_id,
        })
        if triplet_id:
            self.log.debug("Triplet %s geloggt", triplet_id)

        if not settings.extract_facts or d.get("zone") != ZONE_TRUST:
            return
        if d.get("plan_id"):
            return  # Plan-Ausführungsprotokolle sind keine Fakten-Quelle
        staged = self._extract_to_staging(text)
        if staged:
            self.staged_total += len(staged)
            self.log.info("%d Tripel ins Staging (gesamt %d) — Review: ossifikat-CLI",
                          len(staged), self.staged_total)
            self.progress(msg.correlation_id, "facts_staged",
                          f"{len(staged)} Fakten-Kandidaten im Staging")

    def _extract_to_staging(self, text: str) -> list:
        try:
            from ossifikat.store import OssifikatStore
            db = Path(settings.ossifikat_db)
            db.parent.mkdir(parents=True, exist_ok=True)
            store = OssifikatStore(str(db))
            try:
                return self._get_extractor().extract_and_stage(
                    text, store, source="collect2-learning")
            finally:
                store.close()
        except Exception as e:
            self.log.warning("Fakt-Extraktion fehlgeschlagen: %s", e)
            return []

    def _get_extractor(self):
        if self._extractor is None:
            from ossifikat.extractor import QwenExtractor
            self._extractor = QwenExtractor(
                model=settings.main_model, ollama_url=settings.ollama_url)
        return self._extractor
