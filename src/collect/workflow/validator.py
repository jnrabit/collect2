"""Validator — Code-Qualitäts-Checks jenseits simpler Security-Patterns.

Port der validator2-Cross-File-Checks aus vibelike (666 LOC → ~220 LOC hier,
nur die für den Code-Workflow relevanten Checks). Drei Einsatzpunkte:
  1. Nach Plan-Generierung: Plan-Drift + halluzinierte Dateien
  2. Nach Code-Generierung: Cross-File-Import/Export-Abgleich
  3. Vor Commit: Test-Coverage-Awareness

Transportfrei, direkt testbar — kein LLM, kein Redis.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional


def extract_top_level_symbols(source: str) -> dict[str, set[str]]:
    """→ {functions, classes, constants}. Leere Sets bei Syntax-Fehlern."""
    out = {"functions": set(), "classes": set(), "constants": set()}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out["functions"].add(node.name)
        elif isinstance(node, ast.ClassDef):
            out["classes"].add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    out["constants"].add(target.id)
    return out


def extract_imports(source: str) -> dict[str, set[str]]:
    """→ {module_name: {imported_names}}. Names=None für `import foo`."""
    out: dict[str, set[str]] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.name] = out.get(alias.name, set())
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            out[node.module] = out.get(node.module, set()) | {
                alias.name for alias in node.names if alias.name != "*"
            }
    return out


def find_cross_file_issues(generated_files: dict[str, str]) -> list[dict]:
    """Prüft Importe in generierten Dateien gegen Exporte in anderen generierten Dateien.
    → [{kind, file, detail}, …]. `generated_files`: {pfad: quelltext}."""
    issues = []
    exports: dict[str, set[str]] = {}
    for path, src in generated_files.items():
        mod_name = Path(path).stem
        syms = extract_top_level_symbols(src)
        all_syms = syms["functions"] | syms["classes"] | syms["constants"]
        if all_syms:
            exports[mod_name] = all_syms

    for path, src in generated_files.items():
        imports = extract_imports(src)
        for module, names in imports.items():
            if module in exports and names:
                missing = names - exports[module]
                if missing:
                    issues.append({
                        "kind": "missing_export",
                        "file": path,
                        "detail": f"Importiert {sorted(missing)[:4]} aus '{module}', "
                                  f"aber '{module}' exportiert diese Symbole nicht. "
                                  f"Exportiert: {sorted(exports[module])[:5]}",
                    })
    return issues


def find_plan_drift(plan_files: list[dict], generated_files: dict[str, str]) -> list[dict]:
    """Vergleicht Plan-Beschreibungen mit generierten Symbolen.
    → [{kind, file, detail}, …]."""
    issues = []
    plan_map = {f["path"]: f for f in plan_files}

    for path, src in generated_files.items():
        plan = plan_map.get(path, {})
        desc = (plan.get("description", "") or "").lower()
        syms = extract_top_level_symbols(src)
        gen = syms["functions"] | syms["classes"]

        if not gen:
            continue

        desc_words = set(desc.replace("_", " ").replace("-", " ").split())

        for name in gen:
            name_parts = set(name.lower().split("_"))
            name_in_desc = bool(desc_words & name_parts)
            if not name_in_desc and desc:
                issues.append({
                    "kind": "plan_drift",
                    "file": path,
                    "detail": f"'{name}' generiert, aber nicht im Plan beschrieben. "
                              f"Plan: '{plan.get('description', '?')[:80]}'",
                })
    return issues


def find_hallucinated_files(plan_files: list[dict], repo: Path) -> list[dict]:
    """Prüft, ob der Plan existierende Dateien referenziert, die nicht im Repo sind.
    Neue Dateien (action='create') sind ausgenommen. → [{kind, file, detail}]."""
    issues = []
    for f in plan_files:
        action = f.get("action", "create")
        if action == "create":
            continue
        target = repo / f["path"]
        if not target.exists():
            issues.append({
                "kind": "hallucinated_file",
                "file": f["path"],
                "detail": f"Plan referenziert '{f['path']}' (action={action}), "
                          f"aber Datei existiert nicht im Repo.",
            })
    return issues


def find_test_gaps(generated_files: dict[str, str]) -> list[dict]:
    """Prüft, ob jede nicht-Test-Datei eine passende Test-Datei hat.
    → [{kind, file, detail}, …]."""
    issues = []
    sources = {p: s for p, s in generated_files.items()
               if "test_" not in Path(p).name and "_test" not in Path(p).name}
    tests = {p: s for p, s in generated_files.items()
             if "test_" in Path(p).name or Path(p).name.endswith("_test.py")}

    for path, src in sources.items():
        syms = extract_top_level_symbols(src)
        funcs = syms["functions"]
        if not funcs:
            continue
        stem = Path(path).stem
        expected_tests = [f"test_{stem}.py", f"{stem}_test.py",
                          f"tests/test_{stem}.py", f"tests/{stem}_test.py"]
        found = any(et in tests for et in expected_tests)
        if not found:
            issues.append({
                "kind": "missing_tests",
                "file": path,
                "detail": f"{len(funcs)} Funktion(en) ohne Test-Datei. "
                          f"Erwartet: test_{stem}.py oder {stem}_test.py",
            })
    return issues


def validate_workflow(ctx) -> list[dict]:
    """Führt alle relevanten Checks für den WorkflowContext aus.
    → Liste von Issue-Dicts: [{kind, file, detail}, …]."""
    from collect.config import settings

    issues = []
    repo = Path(ctx.repo)
    plan_files = ctx.plan.get("files", [])
    plan_text = ctx.plan.get("strategy", "")

    generated: dict[str, str] = {}
    for c in ctx.changes:
        if c.get("status") in ("written", "repaired"):
            target = repo / c["path"]
            if target.exists():
                try:
                    generated[c["path"]] = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass

    if generated and len(generated) > 1:
        issues += find_cross_file_issues(generated)

    issues += find_hallucinated_files(plan_files, repo)
    issues += find_test_gaps(generated)

    if generated and plan_text:
        drift = find_plan_drift(plan_files, generated)
        if len(drift) <= 2:
            issues += drift

    return issues
