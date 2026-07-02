#!/usr/bin/env python3
"""
Regression-Guard — deterministischer Schutz gegen destruktive Code-Änderungen.

Portiert aus vibelike (dort in Produktion bewährt). Fängt was LLM-Gates
durchlassen: stilles Verschwinden von top-level Symbolen (Klasse/Funktion/
Konstante) und Datei-Kollaps (>50% Schrumpfung). Rein AST/Zeilen-basiert.

ZWEI Einsatzpunkte (gleiche Logik):
  1. Programmatisch — `check_paths()` prüft geplante Writes vor dem Schreiben.
  2. CLI — prüft git-Diffs (staged / range / working tree), auch für direkte
     Edits, die nie durch einen Workflow liefen. Läuft im Repo-Root (git cwd).

CLI (via `collect-guard` oder python -m collect.regression_guard):
  collect-guard --staged          # gegen HEAD gestagte Änderungen
  collect-guard                   # working tree gegen HEAD
  collect-guard --range A..B      # zwei Commits/Refs
  collect-guard --commit <sha>    # ein Commit gegen seinen Parent

Exit-Code: 0 wenn 🟢/🟡, 1 wenn 🔴 (für Pre-Commit-Hooks / CI).
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from typing import Dict, List, Optional, Set

REMOVAL_VERBS = ("remove", "entfern", "löschen", "loeschen", "delete", "wegwerfen", "drop")
SIZE_MIN_LINES = 20      # kleinere Dateien sind zu rauschig für den Kollaps-Check
SIZE_COLLAPSE_RATIO = 0.5


def top_level_symbols(source: str) -> Optional[Set[str]]:
    """Top-level Klassen/Funktionen/Konstanten via AST. None bei Syntaxfehler."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


def check_change(path: str, old: str, new: str, plan_text: str = "") -> List[dict]:
    """Vergleicht alte vs neue Datei-Version. Returns Liste von Issue-Dicts.

    plan_text: optionaler Begründungstext; ein Symbol-Verlust gilt als autorisiert,
    wenn der Symbolname UND ein Entfernungs-Verb darin vorkommen.
    """
    issues: List[dict] = []
    plan_lower = (plan_text or "").lower()

    if path.endswith(".py"):
        old_syms = top_level_symbols(old)
        new_syms = top_level_symbols(new)
        if old_syms is not None and new_syms is not None:
            lost = old_syms - new_syms
            unauthorized = {
                s for s in lost
                if not (s.lower() in plan_lower
                        and any(v in plan_lower for v in REMOVAL_VERBS))
            }
            if unauthorized:
                issues.append({
                    "file": path,
                    "kind": "symbol_loss",
                    "detail": f"{len(unauthorized)} top-level Symbol(e) verschwunden: "
                              f"{sorted(unauthorized)[:8]}",
                })

    old_n = len(old.splitlines())
    new_n = len(new.splitlines())
    if old_n >= SIZE_MIN_LINES and new_n < old_n * SIZE_COLLAPSE_RATIO:
        issues.append({
            "file": path,
            "kind": "size_collapse",
            "detail": f"{old_n} → {new_n} Zeilen ({100*(1-new_n/old_n):.0f}% Reduktion)",
        })
    return issues


def verdict_for(issues: List[dict]) -> str:
    """🔴 bei Symbol-Verlust, 🟡 bei reinem Größen-Kollaps, sonst 🟢.

    symbol_moved ist informativ (Cross-File-Verschiebung) und eskaliert NICHT.
    """
    if any(i["kind"] == "symbol_loss" for i in issues):
        return "🔴"
    if any(i["kind"] == "size_collapse" for i in issues):
        return "🟡"
    return "🟢"


def check_paths(changes: List[dict], plan_text: str = "") -> dict:
    """Programmatischer Pfad: prüft geplante Änderungen (Liste {path, content, exists}).

    Gibt {verdict, issues} zurück. Liest die alte Version vom Dateisystem.
    """
    all_issues: List[dict] = []
    for change in changes:
        if not change.get("exists"):
            continue
        path = change["path"]
        try:
            with open(path, encoding="utf-8") as f:
                old = f.read()
        except OSError:
            continue
        all_issues.extend(check_change(str(path), old, change.get("content", ""), plan_text))
    return {"verdict": verdict_for(all_issues), "issues": all_issues}


# ─────────────────────────── git-basierter CLI-Pfad ───────────────────────────

def _git(args: List[str]) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def _git_ok(args: List[str]) -> tuple[bool, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.returncode == 0, r.stdout


def _changed_py_files(base: str, head: Optional[str], staged: bool,
                      diff_filter: str = "M") -> List[str]:
    if staged:
        out = _git(["diff", "--cached", "--name-only", f"--diff-filter={diff_filter}"])
    elif head:
        out = _git(["diff", "--name-only", f"--diff-filter={diff_filter}", f"{base}..{head}"])
    else:
        out = _git(["diff", "--name-only", f"--diff-filter={diff_filter}", base])
    return [f for f in out.splitlines() if f.endswith(".py")]


def _blob(ref: Optional[str], path: str, staged: bool, working: bool) -> str:
    """Hole Datei-Inhalt für eine Seite des Vergleichs."""
    if working:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""
    spec = f":{path}" if staged else f"{ref}:{path}"
    ok, out = _git_ok(["show", spec])
    return out if ok else ""


def _new_side(path: str, base: str, head: Optional[str], staged: bool) -> str:
    """Neue Version einer Datei (je nach Modus: staged/range/working tree)."""
    if staged:
        return _blob(None, path, staged=True, working=False)
    if head:
        return _blob(head, path, staged=False, working=False)
    return _blob(None, path, staged=False, working=True)


def check_git(base: str = "HEAD", head: Optional[str] = None, staged: bool = False) -> dict:
    """Prüft modifizierte .py-Dateien eines git-Diffs. Returns {verdict, issues, files}.

    Cross-File-Move-Erkennung: ein aus Datei A verschwundenes Symbol, das in einer
    ANDEREN geänderten/neuen Datei desselben Diffs auftaucht, gilt als VERSCHOBEN
    (informativ 🔵), nicht als Zerstörung (🔴) — so übersteht ein Refactor, der Code
    umzieht, den Guard, während echtes Löschen weiter 🔴 bleibt.
    """
    files = _changed_py_files(base, head, staged, diff_filter="M")
    # Symbol-Pool der NEUEN Seite über ALLE geänderten + neu hinzugefügten Dateien
    added = _changed_py_files(base, head, staged, diff_filter="A")
    gained_pool: Set[str] = set()
    new_cache: Dict[str, str] = {}
    for path in set(files) | set(added):
        new_src = _new_side(path, base, head, staged)
        new_cache[path] = new_src
        syms = top_level_symbols(new_src)
        if syms:
            gained_pool |= syms

    all_issues: List[dict] = []
    for path in files:
        old = _blob(base, path, staged=False, working=False)
        new = new_cache.get(path, "")
        for issue in check_change(path, old, new):
            if issue["kind"] == "symbol_loss":
                # Verschoben? Symbole die woanders im Diff wieder auftauchen rausfiltern.
                old_syms = top_level_symbols(old) or set()
                new_syms = top_level_symbols(new) or set()
                lost = old_syms - new_syms
                moved = {s for s in lost if s in gained_pool}
                destroyed = lost - moved
                if moved:
                    all_issues.append({
                        "file": path, "kind": "symbol_moved",
                        "detail": f"{len(moved)} Symbol(e) in andere Datei verschoben: "
                                  f"{sorted(moved)[:8]}",
                    })
                if destroyed:
                    issue["detail"] = (f"{len(destroyed)} top-level Symbol(e) verschwunden: "
                                       f"{sorted(destroyed)[:8]}")
                    all_issues.append(issue)
            else:
                all_issues.append(issue)
    return {"verdict": verdict_for(all_issues), "issues": all_issues, "files": files}


def main() -> int:
    ap = argparse.ArgumentParser(description="Regression-Guard für git-Diffs")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="gestagte Änderungen gegen HEAD")
    g.add_argument("--range", metavar="A..B", help="zwei Refs (z.B. HEAD~1..HEAD)")
    g.add_argument("--commit", metavar="SHA", help="ein Commit gegen seinen Parent")
    args = ap.parse_args()

    if args.range:
        base, _, head = args.range.partition("..")
        result = check_git(base=base or "HEAD", head=head or "HEAD")
    elif args.commit:
        result = check_git(base=f"{args.commit}~1", head=args.commit)
    elif args.staged:
        result = check_git(staged=True)
    else:
        result = check_git()  # working tree vs HEAD

    v = result["verdict"]
    n = len(result.get("files", []))
    print(f"{v} REGRESSION-GUARD — {n} modifizierte .py-Datei(en) geprüft")
    _icons = {"symbol_loss": "🔴", "size_collapse": "🟡", "symbol_moved": "🔵"}
    for issue in result["issues"]:
        icon = _icons.get(issue["kind"], "🟡")
        print(f"  {icon} {issue['file']}: {issue['detail']}")
    if v == "🟢":
        print("  ✓ keine destruktiven Änderungen (🔵 = nur verschoben, ok)")
    return 1 if v == "🔴" else 0


if __name__ == "__main__":
    sys.exit(main())
