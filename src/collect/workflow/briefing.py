"""Briefing — Kontext fürs Planning sammeln: Repo-Struktur + relevante Dateien.

Bewusst deterministisch (kein LLM): Dateibaum, README-Kopf, und die Inhalte
der Dateien, deren Namen Task-Begriffe enthalten. Optional Code-Vault-Treffer.
"""

from __future__ import annotations

from pathlib import Path

from collect.retrieval.service import query_terms

MAX_TREE = 80
MAX_FILE_CHARS = 2500
MAX_MATCHED_FILES = 3
_SKIP = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}


def _tree(repo: Path) -> list[Path]:
    out = []
    for p in sorted(repo.rglob("*")):
        if any(part in _SKIP for part in p.relative_to(repo).parts):
            continue
        if p.is_file():
            out.append(p)
        if len(out) >= MAX_TREE:
            break
    return out


def gather(task: str, repo: Path, code_hits: list | None = None) -> str:
    repo = Path(repo)
    if not repo.is_dir():
        return f"(Repo {repo} existiert noch nicht — Neuanlage)"

    files = _tree(repo)
    parts = ["DATEIEN IM REPO:"]
    parts += [f"  {p.relative_to(repo)}" for p in files] or ["  (leer)"]

    readme = next((p for p in files if p.name.lower().startswith("readme")), None)
    if readme:
        parts += ["", "README (Anfang):",
                  readme.read_text(encoding="utf-8", errors="replace")[:800]]

    # Dateien, deren Name Task-Begriffe enthält → Inhalt zeigen
    terms = query_terms(task)
    matched = [p for p in files
               if any(t in p.name.lower() for t in terms)][:MAX_MATCHED_FILES]
    for p in matched:
        parts += ["", f"INHALT {p.relative_to(repo)}:",
                  p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]]

    for hit in (code_hits or [])[:2]:
        parts += ["", f"CODE-VAULT ({hit.get('title', '?')}):",
                  str(hit.get("content", ""))[:600]]

    return "\n".join(parts)
