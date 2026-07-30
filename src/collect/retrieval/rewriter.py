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

# Pronomen/Deixis, die auf den Vorkontext zeigen (DE + EN). Eindeutig
# rückverweisende Formen; die mehrdeutigen Artikel-Demonstrative stehen
# separat in _DEMONSTRATIVE (siehe dort).
# da-Komposita sind ausnahmslos anaphorisch ("dagegen" = gegen DAS eben
# Genannte) — die Liste deshalb vollstaendig halten, nicht stichprobenartig.
_PRONOUNS = frozenset(
    "das es dies dieser diese dieses er ihn ihm denen da dort "
    "damit dafür dafuer davon dabei dazu daran darauf darüber darueber "
    "dagegen dadurch darin darunter davor danach dahinter daneben darum "
    "daraus dazwischen deshalb deswegen "
    "it that this these those they them its".split())

# Artikel-Demonstrative: "der/die/den/…" zeigen zurück, WENN kein Nomen folgt.
#   "wie erkennt man den?"        → Demonstrativ, braucht den Vorkontext
#   "wie funktioniert der Cache?" → Artikel, Frage steht für sich
# Unterscheidung an der Großschreibung des Folgeworts (deutsche Nomen).
#
# Heuristik, kein Parser: ein vorangestelltes Adjektiv täuscht sie ("die
# degressive Abschreibung" gilt als Demonstrativ). Das ist die guenstige
# Fehlerrichtung — der Rewrite ist additiv (die Originalfrage läuft per RRF
# weiter mit), ein ueberfluessiger Rewrite kostet also wenig, eine uebersehene
# Folgefrage dagegen den Treffer. Gemessen am gesammelten Korpus (2026-07-21):
# 98 eigenstaendige Fragen, davon genau EINE neu falsch-positiv; auf der
# Gegenseite werden Faelle wie "wie erkennt man den?" nicht mehr uebersehen.
_DEMONSTRATIVE = re.compile(r"\b[Dd](?:er|ie|en|em|es|eren|essen)\b(?!\s+[A-ZÄÖÜ])")

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
    return (bool(words & _PRONOUNS)
            or bool(_LEAD_IN.match(q))
            or bool(_DEMONSTRATIVE.search(q)))


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
            generate_fn = _default_generate_fn()
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
    _record_rewrite_trace(prompt, rewritten)
    return {"original": query, "rewritten": rewritten,
            "applied": applied, "duration_ms": (time.time() - t0) * 1000}


def _rewrite_provenance() -> dict:
    """WER hat den Rewrite erzeugt — Modell UND Treiber.

    Ohne diese Angabe ist ein Trace-Bestand nicht auswertbar: 3b-Rewrites und
    Schritte eines staerkeren Modells sind nicht dieselbe Verteilung, und
    derselbe Modellname ueber einen anderen Treiber ist ein anderes Ergebnis.
    Wirft nie — der Rewrite ist wichtiger als sein Etikett.
    """
    try:
        if settings.rewrite_k4n0n3_enabled:
            return {"model": settings.k4n0n3_rewrite_model or settings.k4n0n3_model,
                    "model_digest": None, "quant": None, "driver": "k4n0n3"}
        from collect.agents import ollama
        return ollama.model_provenance(
            settings.rewrite_model or settings.decompose_model)
    except Exception:  # noqa: BLE001
        return {}


# Trace-Einhängung (Auftrag: der Rewriter ist das primäre Verhaltensziel).
# Nur mitschreiben, nie eingreifen — record_if_enabled ist no-op wenn aus und
# wirft nie. step_kind="rewrite"; das Target ist die umgeschriebene Query.
_REWRITE_TRACE_SYSTEM = (
    "Du bist ein Query-Rewriter für ein Retrieval-System. Forme die "
    "referenzielle Folgefrage zu EINER eigenständigen Suchanfrage um, die "
    "ohne den Verlauf verständlich ist. Antworte nur mit der Suchanfrage. "
    "Ist die Frage bereits eigenständig, antworte mit: UNCHANGED"
)


def _default_generate_fn():
    """Baut den generate_fn, wenn keiner uebergeben wurde.

    rewrite_k4n0n3_enabled → der Rewriter (und NUR er) laeuft in-process ueber
    den K4N0N3-Offload-Adapter, mit dem System-Prompt aus dem Training. Damit
    ist der finetunte Qwythos nutzbar, obwohl er ueber Ollama auf dieser
    Hardware nicht laeuft (qwen3_5 braucht MLX). Single-Shot, daher ist die
    Offload-Latenz tragbar. Sonst der bisherige Ollama-Pfad."""
    if settings.rewrite_k4n0n3_enabled:
        from collect.k4n0n3 import generate as k4_generate
        model = settings.k4n0n3_rewrite_model or settings.k4n0n3_model

        def generate_fn(p, **kw):
            # System-Prompt wie im Training mitgeben; der Rewriter-Prompt ist
            # der User-Turn. K4N0N3 rendert per Chat-Template + strippt <think>.
            return k4_generate(p, system=_REWRITE_TRACE_SYSTEM, model=model,
                               temperature=0.0)
        return generate_fn

    from collect.agents import ollama

    def generate_fn(p, **kw):
        # System-Prompt MITGEBEN: gemessen (2026-07-29, qwen3:8b, eval_hard
        # Satz 1) 20/24 mit gegen 17/24 ohne — er kostet nichts und rettet drei
        # Faelle. Bis hierher lief der Ollama-Pfad ohne, obwohl die Traces ihn
        # protokollierten; der K4N0N3-Pfad gab ihn schon immer mit. Damit sind
        # beide Pfade und die aufgezeichneten Traces endlich deckungsgleich.
        return ollama.generate(
            p, system=_REWRITE_TRACE_SYSTEM,
            model=settings.rewrite_model or settings.decompose_model,
            timeout=30.0, temperature=0.0)
    return generate_fn


def _record_rewrite_trace(prompt: str, rewritten: str) -> None:
    try:
        from collect.traces.collector import record_if_enabled
        record_if_enabled("rewrite", [
            {"role": "system", "content": _REWRITE_TRACE_SYSTEM},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": rewritten},
        ], provenance=_rewrite_provenance())
    except Exception:  # noqa: BLE001 — Tracing darf den Rewrite nie brechen
        pass
