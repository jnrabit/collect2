"""Planning — LLM zerlegt den Task in Strategie + konkrete Datei-Änderungen."""

from __future__ import annotations

import json
from pathlib import Path

from collect.config import settings

PROMPT = """Du bist ein Software-Planer. Plane die Umsetzung des Tasks als konkrete Datei-Änderungen.

TASK: {task}

{briefing}

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{{"strategy": "1-2 Sätze Vorgehen",
  "files": [{{"path": "relativer/pfad.py", "action": "create|modify",
              "description": "was genau in dieser Datei passiert"}}]}}

Regeln:
- Maximal {max_files} Dateien, nur relative Pfade innerhalb des Repos.
- Zu JEDER neuen Funktionalität gehört eine Testdatei (tests/test_*.py, pytest).
- Kein Text vor oder nach dem JSON."""


def parse_plan(raw: str) -> dict:
    """Robuste JSON-Extraktion (erstes {...}-Objekt), Pfade validiert."""
    if not raw:
        return {}
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(raw[start:end + 1])
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}

    files = []
    for f in obj.get("files", [])[:settings.workflow_max_files]:
        if not isinstance(f, dict):
            continue
        path = str(f.get("path", "")).strip()
        # Pfad-Hygiene: relativ, kein Ausbruch aus dem Repo
        if not path or path.startswith(("/", "~")) or ".." in Path(path).parts:
            continue
        action = f.get("action", "create")
        files.append({"path": path,
                      "action": action if action in ("create", "modify") else "create",
                      "description": str(f.get("description", ""))[:400]})
    return {"strategy": str(obj.get("strategy", ""))[:600], "files": files}


def plan(task: str, briefing: str, generate) -> dict:
    prompt = PROMPT.format(task=task, briefing=briefing[:6000],
                           max_files=settings.workflow_max_files)
    try:
        raw = generate(prompt, system="Antworte nur mit gültigem JSON.")
        if isinstance(raw, tuple):
            raw = raw[0]
        return parse_plan(raw)
    except Exception:
        return {}
