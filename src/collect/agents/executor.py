"""ExecutorAgent — führt einzelne Plan-Schritte aus (die Act-Rolle).

Grammatik wie im Alt-System (read/write/execute/list_files/search_files/
get_file_info/create_directory/remove_directory), aber mit Sicherheits-
grenzen, die dort fehlten:
  - read/list/search nur unterhalb settings.executor_read_roots
  - write/mkdir/rmdir nur unterhalb settings.executor_workspace
  - Pre-Write-Gate: Syntax-Check (.py) + regression_guard.check_paths
    (Symbol-Verlust/Datei-Kollaps) — validator2-Vollport folgt in Phase 4
  - execute: nur wenn settings.executor_allow_execute (default aus)
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.config import settings
from collect.regression_guard import check_paths
from collect.validation import write_blockers


def _within(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


class StepError(Exception):
    pass


def execute_action(action: str) -> str:
    """Führt eine Aktion aus, gibt ein kompaktes Text-Ergebnis zurück.
    Wirft StepError bei Verstößen/Fehlern. Transportfrei, direkt testbar."""
    head, _, arg = action.partition(":")
    arg = arg.strip()
    read_roots = settings.executor_read_roots
    workspace = Path(settings.executor_workspace)

    if head == "read":
        p = Path(arg)
        if not _within(p, read_roots):
            raise StepError(f"read außerhalb erlaubter Wurzeln: {arg}")
        if not p.is_file():
            raise StepError(f"Datei nicht gefunden: {arg}")
        text = p.read_text(encoding="utf-8", errors="replace")
        return text[:2000] + ("…" if len(text) > 2000 else "")

    if head == "write":
        path_str, _, content = arg.partition("|")
        p = Path(path_str.strip())
        if not p.is_absolute():
            p = workspace / p
        if not _within(p, [workspace]):
            raise StepError(f"write außerhalb des Workspace: {p}")
        if p.suffix == ".py":
            try:
                ast.parse(content)
            except SyntaxError as e:
                raise StepError(f"Syntax-Fehler im Inhalt: {e}") from e
        gate = check_paths([{"path": str(p), "content": content,
                             "exists": p.exists()}])
        if gate["verdict"] == "🔴":
            details = "; ".join(i["detail"] for i in gate["issues"])
            raise StepError(f"Regression-Guard blockt Write: {details}")
        blockers = write_blockers(content, str(p))
        if blockers:
            raise StepError(f"Security-Scan blockt Write: {'; '.join(blockers[:3])}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"{len(content)} Zeichen → {p}"

    if head == "execute":
        if not settings.executor_allow_execute:
            raise StepError("execute: deaktiviert (COLLECT_EXECUTOR_ALLOW_EXECUTE=true zum Aktivieren)")
        parts = arg.split()
        r = subprocess.run(parts, capture_output=True, text=True,
                           timeout=settings.step_timeout, cwd=str(workspace))
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            raise StepError(f"Exit {r.returncode}: {out[:400]}")
        return out[:800] or "(kein Output)"

    if head == "list_files":
        p = Path(arg or str(workspace))
        if not _within(p, read_roots):
            raise StepError(f"list_files außerhalb erlaubter Wurzeln: {arg}")
        if not p.is_dir():
            raise StepError(f"Kein Verzeichnis: {p}")
        entries = sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir())
        return "\n".join(entries[:100]) or "(leer)"

    if head == "search_files":
        matches = []
        for root in read_roots:
            root = Path(root)
            if root.is_dir():
                matches.extend(str(m) for m in root.rglob(arg)
                               if "/.git/" not in str(m) and "/.venv/" not in str(m))
            if len(matches) >= 50:
                break
        return "\n".join(matches[:50]) or "(keine Treffer)"

    if head == "get_file_info":
        p = Path(arg)
        if not _within(p, read_roots):
            raise StepError(f"get_file_info außerhalb erlaubter Wurzeln: {arg}")
        if not p.exists():
            raise StepError(f"Nicht gefunden: {arg}")
        st = p.stat()
        kind = "dir" if p.is_dir() else "file"
        return f"{kind}, {st.st_size} bytes, mtime={int(st.st_mtime)}"

    if head == "create_directory":
        p = Path(arg)
        if not p.is_absolute():
            p = workspace / p
        if not _within(p, [workspace]):
            raise StepError(f"create_directory außerhalb des Workspace: {p}")
        p.mkdir(parents=True, exist_ok=True)
        return f"Verzeichnis angelegt: {p}"

    if head == "remove_directory":
        p = Path(arg)
        if not p.is_absolute():
            p = workspace / p
        if not _within(p, [workspace]):
            raise StepError(f"remove_directory außerhalb des Workspace: {p}")
        if not p.is_dir():
            raise StepError(f"Kein Verzeichnis: {p}")
        try:
            p.rmdir()  # bewusst nur leere Verzeichnisse — kein rekursives Löschen
        except OSError as e:
            raise StepError(f"Nicht leer/löschbar: {e}") from e
        return f"Verzeichnis entfernt: {p}"

    raise StepError(f"Unbekannte Aktion: {action!r}")


class ExecutorAgent(BaseAgent):
    name = "executor"

    def subscriptions(self):
        return {"step_request": self.on_step}

    def on_step(self, msg: Message) -> None:
        action = msg.data.get("action", "")
        base = {"plan_id": msg.data.get("plan_id"),
                "step_id": msg.data.get("step_id")}
        try:
            result = execute_action(action)
            self.publish("step_response", "step_response",
                         {**base, "status": "finished", "result": result},
                         msg.correlation_id)
            self.log.info("Schritt %s ok: %s", base["step_id"], action[:60])
        except (StepError, Exception) as e:
            self.publish("step_response", "step_response",
                         {**base, "status": "failed", "error": str(e)},
                         msg.correlation_id)
            self.log.warning("Schritt %s fehlgeschlagen: %s", base["step_id"], e)
