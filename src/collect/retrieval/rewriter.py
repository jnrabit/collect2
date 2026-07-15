"""Query-Rewrite für referenzielle Folgefragen (Follow-up-Retrieval).

„und wofür kann man das nutzen?" retrievt kontextlos ins Leere. Ein
deterministisches Gate erkennt referenzielle Fragen (kurz + Pronomen/Deixis
oder Konjunktions-Anfang); nur die gehen an qwen2.5:3b, das aus den letzten
Gesprächs-Turns eine eigenständige Frage formt. Wirkung ist additiv: die
Original-Query läuft als zusätzliche Subquery mit (RRF-Fusion) — schlechtes
Rewriting kann das Ergebnis nie unter den Status quo drücken. Zonen-Logik
und Gewichte bleiben unberührt.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

from collect import prompts
from collect.config import settings
from collect.retrieval.service import query_terms

logger = logging.getLogger(__name__)

# mehr Inhaltswörter → eigenständige Frage. Konfigurierbar
# (COLLECT_REWRITE_MAX_CONTENT_TERMS); Alias bindet beim Start.
MAX_CONTENT_TERMS = settings.rewrite_max_content_terms

# Pronomen/Deixis, die auf den Vorkontext zeigen (DE + EN). Bewusst OHNE
# Artikel (der/die/das als Artikel) — nur eindeutig rückverweisende Formen.
_PRONOUNS = frozenset(
    "das es dies dieser diese dieses damit dafür dafuer davon dabei dazu "
    "daran darauf darüber darueber deshalb deswegen er ihn ihm "
    "it that this these those they them its".split())

# Konjunktions-/Anschluss-Anfänge, die einen vorherigen Turn voraussetzen
_LEAD_IN = re.compile(
    r"^(und|aber|oder|also|and|but|or|so)\b|^(was|what|how)\s+(ist|is|about)\s+(mit|with)\b",
    re.IGNORECASE)

# Text zentral in collect.prompts (extern überschreibbar via
# COLLECT_PROMPTS_DIR/rewrite.txt); Alias für bestehende Importe.
REWRITE_PROMPT = prompts.embedded("rewrite")


def is_referential(query: str) -> bool:
    """Deterministisches Gate: braucht diese Frage den Vorkontext?"""
    q = query.strip()
    if not q:
        return False
    if len(query_terms(q)) > settings.rewrite_max_content_terms:
        return False
    words = set(re.findall(r"[a-zäöüß]+", q.lower()))
    return bool(words & _PRONOUNS) or bool(_LEAD_IN.match(q))


def _clean(raw: str, original: str) -> str:
    s = (raw or "").strip().strip("\"'").split("\n")[0].strip()
    if not (3 <= len(s) <= 300):
        return original
    return s


def rewrite(query: str, history: list,
            generate_fn: Optional[Callable] = None) -> dict:
    """→ {original, rewritten, applied, duration_ms}. applied=False ⇒
    rewritten == original (Gate zu, Historie leer, deaktiviert oder LLM weg)."""
    t0 = time.time()
    base = {"original": query, "rewritten": query,
            "applied": False, "duration_ms": 0.0}
    if not settings.rewrite_enabled or not history or not is_referential(query):
        return base

    lines = []
    for turn in history[-2:]:
        lines.append(f"Nutzer: {str(turn.get('q', ''))[:200]}")
        lines.append(f"Assistent: {str(turn.get('a', ''))[:300]}")
    prompt = prompts.get_prompt("rewrite").format(
        history="\n".join(lines), query=query)

    try:
        if generate_fn is None:
            from collect.agents import ollama

            def generate_fn(p, **kw):
                return ollama.generate(
                    p, model=settings.rewrite_model or settings.decompose_model,
                    timeout=15.0, temperature=0.0)
        raw = generate_fn(prompt)
        if isinstance(raw, tuple):
            raw = raw[0]
        rewritten = _clean(raw, query)
    except Exception as e:
        logger.warning("Rewrite fehlgeschlagen (Fallback Original): %s", e)
        return base

    applied = rewritten != query
    if applied:
        logger.info("Rewrite: %r → %r", query[:50], rewritten[:70])
    return {"original": query, "rewritten": rewritten,
            "applied": applied, "duration_ms": (time.time() - t0) * 1000}
