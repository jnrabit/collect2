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
_RULES: list[tuple[str, frozenset, tuple]] = [
    ("chaos",
     frozenset({"entdeck", "zufällig", "zufaellig", "überrasch", "ueberrasch",
                "ungewöhnlich", "ungewoehnlich", "inspiration", "brainstorm",
                "ideen", "kreativ", "random", "unexpected"}),
     ("quer denken", "quer gedacht")),
    ("broad",
     frozenset({"alternative", "weitere", "verschiedene", "auflisten",
                "liste", "sammle", "übersicht", "uebersicht", "überblick",
                "ueberblick", "ähnlich", "aehnlich"}),
     ("gibt es noch", "mehr dazu", "andere ansätze", "andere möglichkeiten",
      "andere ansaetze", "andere möglichkeit")),
    ("resonant",
     frozenset({"warum", "weshalb", "wieso", "zusammenhang", "vergleich",
                "unterschied", "gemeinsamkeit", "entwicklung", "geschichte",
                "historie", "vertief", "detail", "genauer", "tiefer"}),
     ("wie hängt", "wie haengt", "hängt zusammen", "haengt zusammen")),
    ("precise",
     frozenset({"definition", "definier", "erklär", "erklaer", "beschreib",
                "bedeutung", "define", "explain", "describe"}),
     ("was ist", "was sind", "was bedeutet", "what is", "wie funktioniert")),
]


def parse_profile_override(query: str) -> tuple[str, Optional[str]]:
    """Entfernt [profil]-Prefix aus der Query. → (bereinigte_query, profil|None)."""
    m = _PREFIX_PATTERN.match(query)
    if m:
        return (query[m.end():].strip(), m.group(1).lower())
    return (query, None)


def auto_detect(query: str) -> str:
    """Deterministische Keyword-Heuristik → bestes Profil. Default: 'balanced'.
    Feste Regel-Priorität (_RULES-Reihenfolge) statt set-Iteration — das
    Ergebnis hängt nie von der Hash-Randomisierung ab."""
    q = query.lower()
    words = re.findall(r"[a-zäöüß]+", q)
    for profile, stems, phrases in _RULES:
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
