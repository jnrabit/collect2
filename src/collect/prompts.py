"""Prompt-Registry — alle LLM-Prompts zentral, per Datei überschreibbar.

COLLECT_PROMPTS_DIR zeigt auf ein Verzeichnis mit <name>.txt-Dateien
(z.B. llm_system.txt). Eine Datei überschreibt den eingebauten Default NUR,
wenn sie valide ist: alle Pflicht-Platzhalter vorhanden, keine unbekannten
Platzhalter (die würden beim .format() zur Laufzeit crashen), keine kaputte
{}-Syntax. Sonst Fallback auf den eingebauten Prompt + Warnung — eine
editierte Datei darf NIE eine Query zum Absturz bringen.

Start-Vorlagen exportieren:  python -m collect.prompts export [verzeichnis]
"""

from __future__ import annotations

import logging
from pathlib import Path
from string import Formatter
from typing import Optional

from collect.config import settings

logger = logging.getLogger(__name__)


# name → (default_text, pflicht_platzhalter | None).
# None = Prompt wird NIE .format()iert (roher Text, {} unkritisch).
# Sonst: required ⊆ gefunden ⊆ erlaubt (erlaubt = Platzhalter des Defaults;
# nur die bekommt der .format()-Aufruf als kwargs).
_REGISTRY: dict[str, tuple[str, Optional[frozenset]]] = {

    # ── Antwort-Synthese (LLMAgent, system-Prompt) ──────────────────────
    "llm_system": (
        "Du bist ein Wissensassistent. Beantworte die Frage des Nutzers auf Deutsch, "
        "GESTÜTZT auf die bereitgestellten Quellen. Antworte AUSFÜHRLICH und gut "
        "strukturiert: erkläre Zusammenhänge, gib relevante Details und Beispiele aus "
        "den Quellen wieder statt nur Stichworte. Nenne die verwendeten Quellen — bei "
        "Web-Quellen mit dem Link (URL). Wenn die Quellen die Frage nicht abdecken, "
        "sage das ehrlich. Erfinde keine Fakten.",
        None),

    # ── Session-Zusammenfassung ─────────────────────────────────────────
    "session_summary": (
        "Du bist ein präziser Zusammenfasser. Verdichte den folgenden Gesprächsverlauf\n"
        "auf 3-5 KERNSÄTZE. Nur Fakten und Ergebnisse, keine Höflichkeitsfloskeln.\n"
        "Schreibe auf Deutsch, maximal 300 Zeichen.\n\n"
        "GESPRÄCH:\n{turns}\n\n"
        "ZUSAMMENFASSUNG:",
        frozenset({"turns"})),

    # ── Pre-Retrieval: Follow-up-Rewrite ────────────────────────────────
    "rewrite": (
        "Formuliere die FOLGEFRAGE als eigenständige, vollständige Frage um, "
        "die ohne das Gespräch verständlich ist. Behalte die Sprache der "
        "Folgefrage bei. Antworte NUR mit der umformulierten Frage — keine "
        "Erklärung, keine Anführungszeichen.\n\n"
        "GESPRÄCH:\n{history}\n\nFOLGEFRAGE: {query}\n\nEigenständige Frage:",
        frozenset({"history", "query"})),

    # ── Pre-Retrieval: DE→EN-Übersetzung ────────────────────────────────
    "translate": (
        "Translate the following German query into concise English suitable "
        "for keyword search in technical/scientific documents. "
        "Keep technical terms (TLS, HTTP, RAM, etc.) unchanged. "
        "Output ONLY the translated query — no quotes, no explanation, no prefix.\n\n"
        "German: {query}\nEnglish:",
        frozenset({"query"})),

    # ── Pre-Retrieval: Mehr-Aspekt-Zerlegung ────────────────────────────
    "decompose": (
        "Break the following search query into 2-3 focused, self-contained sub-queries, "
        "ONE per distinct concept or domain it touches. Each sub-query must stand alone "
        "(no pronouns referring to the others) and be in English, suitable for keyword/"
        "semantic search in technical & scientific documents. Do NOT add concepts the "
        "query does not mention. If the query is already single-topic, return it unchanged "
        "as the only element.\n\n"
        "Query: {query}\n"
        'Respond as JSON: {{"subqueries": ["...", "..."]}}',
        frozenset({"query"})),

    # ── Code-Workflow: Planung ──────────────────────────────────────────
    "workflow_planning": (
        "Du bist ein Software-Planer. Plane die Umsetzung des Tasks als konkrete Datei-Änderungen.\n\n"
        "TASK: {task}\n\n"
        "{briefing}\n\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:\n"
        '{{"strategy": "1-2 Sätze Vorgehen",\n'
        '  "files": [{{"path": "relativer/pfad.py", "action": "create|modify",\n'
        '              "description": "was genau in dieser Datei passiert"}}]}}\n\n'
        "Regeln:\n"
        "- Maximal {max_files} Dateien, nur relative Pfade innerhalb des Repos.\n"
        "- Zu JEDER neuen Funktionalität gehört eine Testdatei (tests/test_*.py, pytest).\n"
        "- Kein Text vor oder nach dem JSON.",
        frozenset({"task"})),

    # ── Code-Workflow: Datei-Generierung ────────────────────────────────
    "workflow_codegen": (
        "Du bist ein präziser Software-Entwickler. Schreibe den VOLLSTÄNDIGEN Inhalt einer Datei.\n\n"
        "TASK: {task}\n"
        "STRATEGIE: {strategy}\n"
        "{written}\n"
        "DATEI: {path}\n"
        "AUFGABE DIESER DATEI: {description}\n"
        "{current}\n"
        "Regeln:\n"
        "- Gib den KOMPLETTEN Datei-Inhalt aus (kein Diff, keine Auslassungen).\n"
        "- Bei modify: bestehende Funktionen/Klassen BEIBEHALTEN, außer der Plan sagt anderes.\n"
        "- Tests: importiere aus den BEREITS GESCHRIEBENEN Dateien; Erwartungswerte\n"
        "  müssen sich EXAKT aus deren Code ergeben — lieber wenige, korrekte Assertions.\n"
        "- Antworte NUR mit einem Code-Block:\n"
        "```python\n"
        "...\n"
        "```",
        frozenset({"task", "path"})),

    # ── Code-Workflow: Repair-Runde ─────────────────────────────────────
    "workflow_repair": (
        "Du bist ein präziser Software-Entwickler. Die Tests schlagen fehl — repariere GENAU EINE Datei.\n\n"
        "TASK: {task}\n\n"
        "TEST-FEHLER:\n{failure}\n\n"
        "DATEIEN IM REPO:\n{files}\n\n"
        "ENTSCHEIDE zuerst, WO der Fehler liegt — prüfe die TATSÄCHLICHE Ausgabe im Fehler gegen den TASK:\n"
        "- Erfüllt die tatsächliche Ausgabe den TASK NICHT → die IMPLEMENTIERUNG ist falsch, fixe sie.\n"
        "- Erfüllt sie den TASK, weicht nur die Test-Erwartung ab → fixe die TESTDATEI (Erwartung = tatsächliche Ausgabe).\n"
        "- NameError/ImportError → meist fehlender Import in der Testdatei.\n"
        "Antworte in GENAU diesem Format — erst die Pfad-Zeile, dann der komplette korrigierte Inhalt:\n"
        "PFAD: <relativer/pfad.py>\n"
        "```python\n"
        "...vollständiger Datei-Inhalt...\n"
        "```",
        frozenset({"task", "failure"})),
}


def _placeholders(text: str) -> Optional[set]:
    """Format-Feldnamen im Text; None bei kaputter {}-Syntax."""
    try:
        return {f.split(".")[0].split("[")[0]
                for _, f, _, _ in Formatter().parse(text) if f}
    except ValueError:
        return None


def embedded(name: str) -> str:
    """Der eingebaute Default-Text (ohne Datei-Override)."""
    return _REGISTRY[name][0]


def get_prompt(name: str) -> str:
    """Effektiver Prompt: Datei-Override (falls valide) sonst Default."""
    default, required = _REGISTRY[name]
    prompts_dir = settings.prompts_dir
    if not prompts_dir:
        return default
    path = Path(prompts_dir) / f"{name}.txt"
    if not path.is_file():
        return default
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Prompt-Datei %s nicht lesbar (%s) — nutze Default", path, e)
        return default
    if not text.strip():
        logger.warning("Prompt-Datei %s ist leer — nutze Default", path)
        return default
    if required is not None:  # formatierter Prompt → Platzhalter validieren
        found = _placeholders(text)
        allowed = _placeholders(default)
        if found is None:
            logger.warning("Prompt-Datei %s: kaputte {}-Syntax — nutze Default", path)
            return default
        if not required <= found:
            logger.warning("Prompt-Datei %s: Pflicht-Platzhalter %s fehlen — "
                           "nutze Default", path, sorted(required - found))
            return default
        if not found <= allowed:
            logger.warning("Prompt-Datei %s: unbekannte Platzhalter %s (würden "
                           "beim Ausfüllen crashen) — nutze Default",
                           path, sorted(found - allowed))
            return default
    return text


def export_defaults(target_dir: Path) -> list[Path]:
    """Schreibt alle Default-Prompts als <name>.txt — Startpunkt fürs Editieren.
    Existierende Dateien werden NICHT überschrieben."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (default, _) in sorted(_REGISTRY.items()):
        path = target / f"{name}.txt"
        if path.exists():
            continue
        path.write_text(default, encoding="utf-8")
        written.append(path)
    return written


def main() -> int:  # python -m collect.prompts export [dir]
    import sys
    args = sys.argv[1:]
    if not args or args[0] != "export":
        print(__doc__)
        print("Prompts:", ", ".join(sorted(_REGISTRY)))
        return 0
    target = Path(args[1]) if len(args) > 1 else (
        settings.prompts_dir or Path("data/prompts"))
    written = export_defaults(target)
    for p in written:
        print(f"✓ {p}")
    print(f"{len(written)} Datei(en) exportiert nach {target} — "
          f"aktivieren mit COLLECT_PROMPTS_DIR={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
