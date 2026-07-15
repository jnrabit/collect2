"""Pattern-Registry — Erkennungs-Wortlisten zentral, per JSON überschreibbar.

COLLECT_PATTERNS_DIR zeigt auf ein Verzeichnis mit <name>.json-Dateien.
Damit lassen sich die Wortlisten erweitern, die Query-Routing steuern
(Plan-/Code-Erkennung, Profil-Signale), ohne Python zu editieren.

Eine Datei überschreibt den eingebauten Default NUR, wenn sie valide ist
(Schema, keine leeren Strings, kompilierbare Regex-Fragmente) — sonst
Fallback auf den Default + Warnung. Eine editierte Datei darf NIE das
Routing crashen oder ein Alles-matcht-Pattern einschleusen.

BEWUSST NICHT hier: die Security-Patterns aus collect.validation — das ist
ein Sicherheits-Gate und bleibt im Code (keine stille Abschwächung per JSON).

Start-Vorlagen exportieren:  python -m collect.patterns export [verzeichnis]
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from collect.config import settings

logger = logging.getLogger(__name__)

_PROFILE_NAMES = frozenset(
    {"precise", "balanced", "broad", "resonant", "chaos", "adaptive"})


# ── Eingebaute Defaults ───────────────────────────────────────────────────
# Listen-Einträge sind Regex-FRAGMENTE (werden zu \b(a|b|…)\b gejoint):
# einfache Wörter funktionieren immer, wer mag nutzt (s|es)?-Endungen.

_DEFAULTS: dict = {
    # Plan-Erkennung: Substantiv reicht; Imperativ nur zusammen mit Objekt
    # (verhindert 'Erkläre mir …'-Fehltreffer). Logik in orchestrator.is_plan_query.
    "plan_detection": {
        "nouns": ["plan", "pläne", "plaene", "ablaufplan(s|es)?",
                  "aufgabenplan(s|es)?", "roadmap", "workflow",
                  "schritte", "steps"],
        "imperatives": ["erstelle?", "plane?", "organisiere?",
                        "koordiniere?", "strukturiere?",
                        "create", "organize", "coordinate"],
        "objects": ["schritte", "steps", "verzeichnis", "datei(en)?",
                    "directory", "file", "struktur"],
    },
    # Code-Task: Implementier-Verb + Code-Objekt (konservativ — Wissensfragen
    # über Code gehen weiter den Retrieval-Weg). Logik in orchestrator.is_code_task.
    "code_detection": {
        "verbs": ["implementiere?", "implement", "refaktoriere?", "refactor",
                  "fixe?", "bugfix", "schreibe?", "write", "baue?", "build"],
        "objects": ["funktion(en)?", "function(s)?", "klasse(n)?", "class(es)?",
                    "methode(n)?", "method(s)?", "modul(e)?", "module(s)?",
                    "test(s)?", "skript(e)?", "script(s)?", "bug(s)?", "code"],
    },
    # Profil-Auto-Detect: LISTEN-REIHENFOLGE = Priorität (spezifisch → generisch;
    # das häufige "was ist" zuletzt). stems matchen als Wort-Präfix,
    # phrases als Substring der ganzen Query. Logik in profiles.auto_detect.
    "profile_signals": [
        {"profile": "chaos",
         "stems": ["entdeck", "zufällig", "zufaellig", "überrasch",
                   "ueberrasch", "ungewöhnlich", "ungewoehnlich",
                   "inspiration", "brainstorm", "ideen", "kreativ",
                   "random", "unexpected"],
         "phrases": ["quer denken", "quer gedacht"]},
        {"profile": "broad",
         "stems": ["alternative", "weitere", "verschiedene", "auflisten",
                   "liste", "sammle", "übersicht", "uebersicht",
                   "überblick", "ueberblick", "ähnlich", "aehnlich"],
         "phrases": ["gibt es noch", "mehr dazu", "andere ansätze",
                     "andere möglichkeiten", "andere ansaetze",
                     "andere möglichkeit"]},
        {"profile": "resonant",
         "stems": ["warum", "weshalb", "wieso", "zusammenhang", "vergleich",
                   "unterschied", "gemeinsamkeit", "entwicklung",
                   "geschichte", "historie", "vertief", "detail",
                   "genauer", "tiefer"],
         "phrases": ["wie hängt", "wie haengt", "hängt zusammen",
                     "haengt zusammen"]},
        {"profile": "precise",
         "stems": ["definition", "definier", "erklär", "erklaer",
                   "beschreib", "bedeutung", "define", "explain",
                   "describe"],
         "phrases": ["was ist", "was sind", "was bedeutet", "what is",
                     "wie funktioniert"]},
    ],
}


# ── Validierung ───────────────────────────────────────────────────────────

def _valid_fragment_list(items) -> bool:
    """Nicht-leere Liste nicht-leerer Strings, jedes Fragment kompilierbar.
    Leere Strings würden \\b(|…)\\b zum Alles-Matcher machen — hart ablehnen."""
    if not isinstance(items, list) or not items:
        return False
    for s in items:
        if not isinstance(s, str) or not s.strip():
            return False
        try:
            re.compile(s)
        except re.error:
            return False
    return True


def _validate(name: str, data) -> Optional[str]:
    """→ Fehlertext oder None (valide)."""
    if name in ("plan_detection", "code_detection"):
        keys = set(_DEFAULTS[name])
        if not isinstance(data, dict) or set(data) != keys:
            return f"erwartet Objekt mit genau den Keys {sorted(keys)}"
        for k in keys:
            if not _valid_fragment_list(data[k]):
                return (f"'{k}' muss eine nicht-leere Liste nicht-leerer, "
                        f"kompilierbarer Regex-Fragmente sein")
        return None
    if name == "profile_signals":
        if not isinstance(data, list) or not data:
            return "erwartet nicht-leere Liste von Regel-Objekten"
        for rule in data:
            if not isinstance(rule, dict):
                return "jede Regel muss ein Objekt sein"
            if rule.get("profile") not in _PROFILE_NAMES:
                return (f"unbekanntes Profil {rule.get('profile')!r} "
                        f"(erlaubt: {sorted(_PROFILE_NAMES)})")
            stems, phrases = rule.get("stems", []), rule.get("phrases", [])
            for lst in (stems, phrases):
                if not isinstance(lst, list) or any(
                        not isinstance(s, str) or not s.strip() for s in lst):
                    return "stems/phrases müssen Listen nicht-leerer Strings sein"
            if not stems and not phrases:
                return f"Regel '{rule['profile']}': stems ODER phrases nötig"
        return None
    return f"unbekannter Pattern-Name {name!r}"


# ── Zugriff ───────────────────────────────────────────────────────────────

def embedded(name: str):
    """Der eingebaute Default (ohne Datei-Override)."""
    return _DEFAULTS[name]


def get_patterns(name: str):
    """Effektive Patterns: Datei-Override (falls valide) sonst Default."""
    default = _DEFAULTS[name]
    patterns_dir = settings.patterns_dir
    if not patterns_dir:
        return default
    path = Path(patterns_dir) / f"{name}.json"
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Pattern-Datei %s nicht lesbar/kein JSON (%s) — "
                       "nutze Default", path, e)
        return default
    err = _validate(name, data)
    if err:
        logger.warning("Pattern-Datei %s invalide (%s) — nutze Default", path, err)
        return default
    return data


@lru_cache(maxsize=64)
def word_regex(fragments: tuple) -> re.Pattern:
    """\\b(a|b|…)\\b aus Regex-Fragmenten, case-insensitive, gecacht."""
    return re.compile(r"\b(" + "|".join(fragments) + r")\b", re.IGNORECASE)


def export_defaults(target_dir: Path) -> list[Path]:
    """Schreibt alle Defaults als <name>.json — Startpunkt fürs Editieren.
    Existierende Dateien werden NICHT überschrieben."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, default in sorted(_DEFAULTS.items()):
        path = target / f"{name}.json"
        if path.exists():
            continue
        path.write_text(json.dumps(default, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        written.append(path)
    return written


def main() -> int:  # python -m collect.patterns export [dir]
    import sys
    args = sys.argv[1:]
    if not args or args[0] != "export":
        print(__doc__)
        print("Patterns:", ", ".join(sorted(_DEFAULTS)))
        return 0
    target = Path(args[1]) if len(args) > 1 else (
        settings.patterns_dir or Path("data/patterns"))
    written = export_defaults(target)
    for p in written:
        print(f"✓ {p}")
    print(f"{len(written)} Datei(en) exportiert nach {target} — "
          f"aktivieren mit COLLECT_PATTERNS_DIR={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
