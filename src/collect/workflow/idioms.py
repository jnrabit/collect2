"""Idiom-System — Few-Shot-Code-Patterns für den Code-Workflow.

Port-Idee aus vibelike (36 Idioms, 855 LOC YAML, embedding-basiertes Routing),
aber radikal vereinfacht: statt Embedding-Router + Feedback-Loop reichen hier
keyword-basierte Task-Typ-Erkennung + feste Few-Shot-Injektion. Die vibelike-
Erfahrung zeigt: schon 1-2 passende Beispiele machen den 7B-Coder deutlich
treffsicherer. Ossifikat-Feedback-Loop kann später ergänzt werden.

Idioms werden aus der paket-internen idioms.json geladen (Feature-Config,
kein User-Data → gehört ins Repo, nicht ins gitignorte data/); der Store ist
transportfrei und direkt testbar.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from collect.config import settings

logger = logging.getLogger(__name__)

IDIOMS_PATH = Path(__file__).parent / "idioms.json"

CATEGORY_KEYWORDS = {
    "function": r"\bfunktion\b|\bfunction\b|\bmethode\b|\bmethod\b|\bdef\b",
    "class": r"\bklasse\b|\bclass\b|\bobjekt\b|\bobject\b|\bdataclass\b",
    "regex": r"\bregex\b|\bpattern\b|\bmuster\b|\bmatch\b|\bpars(e|ing)\b",
    "file_io": r"\bdatei\b|\bfile\b|\blesen\b|\bread\b|\bschreiben\b|\bwrite\b|\bcsv\b|\bjson\b",
    "api": r"\bapi\b|\bendpoint\b|\brequest\b|\bhttp\b|\bclient\b|\bfetch\b",
    "test": r"\btests?\b|\bpytest\b|\bassert\b|\bunittest\b",
    "cli": r"\bcli\b|\bcommand\b|\bargparse\b|\bclick\b|\bbefehl\b",
    "algorithm": r"\bsort\b|\bfilter\b|\bsearch\b|\bsuche\b|\balgorithmus\b|\bmap\b|\breduce\b",
    "string": r"\bstring\b|\btext\b|\bslugify\b|\bformat\b|\bpars(e|ing)\b|\btokenize\b",
    "data": r"\bdaten\b|\bdata\b|\bdatabase\b|\bsql\b|\bquery\b|\btransform\b",
}


def _load_idioms() -> dict:
    if not IDIOMS_PATH.exists():
        logger.warning("Idiom-Datei nicht gefunden: %s — Idiom-System inaktiv", IDIOMS_PATH)
        return {}
    try:
        return json.loads(IDIOMS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Idiom-Load fehlgeschlagen: %s", e)
        return {}


def detect_task_type(task: str) -> str:
    """Keyword-basierte Task-Typ-Erkennung. → category key oder 'general'."""
    import re
    task_lower = task.lower()
    for category, pattern in CATEGORY_KEYWORDS.items():
        if re.search(pattern, task_lower):
            return category
    return "general"


def get_idiom(task_type: str, phase: str) -> Optional[dict]:
    """Idiom für (task_type, phase) aus dem Store. None wenn nicht gefunden."""
    idioms = _load_idioms()
    candidates = idioms.get(task_type, {})
    if not candidates:
        candidates = idioms.get("general", {})
    idiom = candidates.get(phase)
    if idiom:
        logger.info("Idiom %s/%s ausgewählt", task_type, phase)
    return idiom


def inject_idiom(prompt: str, task: str, phase: str) -> str:
    """Few-Shot-Idiom in den Prompt injizieren, falls vorhanden + aktiviert."""
    if not settings.workflow_idioms_enabled:
        return prompt
    task_type = detect_task_type(task)
    idiom = get_idiom(task_type, phase)
    if not idiom:
        return prompt
    example = idiom.get("example", "")
    if not example:
        return prompt
    return prompt + "\n\n" + example


def list_categories() -> list[str]:
    return list(CATEGORY_KEYWORDS.keys())
