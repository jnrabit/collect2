"""Profil-Auto-Detection — wählt das beste Retrieval-Profil per Query-Analyse.

Deterministisch (kein LLM-Call): Keyword-Heuristik + strukturelle Merkmale.
Bei Unsicherheit fällt die Wahl auf 'balanced'. Der Prefix-Override
('[precise] Frage?') hat immer Vorrang vor der Auto-Erkennung.
"""

from __future__ import annotations

import re
from typing import Optional


_PROFILES = {"precise", "balanced", "broad", "resonant", "chaos", "adaptive"}

_PREFIX_PATTERN = re.compile(
    r"^\s*\[(" + "|".join(_PROFILES) + r")\]\s*", re.IGNORECASE)

# Regel-Reihenfolge = Priorität: seltene/spezifische Signale (chaos, broad)
# zuerst, das allgegenwärtige "was ist"-Muster ZULETZT — sonst kapert es
# warum/wozu-Fragen ("warum IST der Himmel blau" ist resonant, nicht precise).
# Wort-Signale matchen als Wort-Präfix (erklär → erkläre/erklären/erklärst),
# Phrasen-Signale als Substring der ganzen Query. Bewusst KEIN bidirektionales
# Substring-Matching: "ist" ⊂ "was ist" würde jeden deutschen Satz treffen.
# Signal-Listen zentral in collect.patterns (extern erweiterbar via
# COLLECT_PATTERNS_DIR/profile_signals.json); _RULES = Alias mit den Defaults.
def _build_rules(signals: list) -> list[tuple[str, frozenset, tuple]]:
    return [(r["profile"], frozenset(r.get("stems", [])),
             tuple(r.get("phrases", []))) for r in signals]


def _default_rules() -> list:
    from collect import patterns
    return _build_rules(patterns.embedded("profile_signals"))


_RULES: list[tuple[str, frozenset, tuple]] = _default_rules()


def parse_profile_override(query: str) -> tuple[str, Optional[str]]:
    """Entfernt [profil]-Prefix aus der Query. → (bereinigte_query, profil|None)."""
    m = _PREFIX_PATTERN.match(query)
    if m:
        return (query[m.end():].strip(), m.group(1).lower())
    return (query, None)


def auto_detect(query: str) -> str:
    """Deterministische Keyword-Heuristik → bestes Profil. Default: 'balanced'.
    Feste Regel-Priorität (Listen-Reihenfolge) statt set-Iteration — das
    Ergebnis hängt nie von der Hash-Randomisierung ab."""
    from collect import patterns
    q = query.lower()
    words = re.findall(r"[a-zäöüß]+", q)
    for profile, stems, phrases in _build_rules(
            patterns.get_patterns("profile_signals")):
        if any(p in q for p in phrases):
            return profile
        if any(w.startswith(s) for w in words for s in stems):
            return profile
    return "balanced"


def resolve_profile(query: str, configured: str = "auto") -> str:
    """Komplette Profil-Resolution: Prefix > config > auto-detect.
    Gibt den effektiven Profil-Namen zurück."""
    cleaned, override = parse_profile_override(query)
    if override:
        return override
    if configured != "auto":
        return configured
    return auto_detect(query)


def clean_query(query: str) -> str:
    """Query-Text ohne Profil-Prefix (fürs Retrieval)."""
    cleaned, _ = parse_profile_override(query)
    return cleaned
