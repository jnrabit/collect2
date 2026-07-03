"""REPL — interaktives Terminal für den Agenten-Stack.

Bewusst klein (die 1.875-LOC-terminal.py aus vibelike ist das Gegenbeispiel):
Fragen stellen, Status sehen, Staging-Tripel reviewen. Das Review ist der
menschliche Teil der Grounding-Schleife — bestätigte Fakten erden ab der
nächsten Query.

    collect-repl
    > Wie funktioniert TLS?     Frage an den Stack
    > /status                   Agenten-Heartbeats
    > /review                   Staging-Tripel bestätigen/verwerfen
    > /facts                    verbürgte Fakten anzeigen
    > /quit
"""

from __future__ import annotations

import sys
from pathlib import Path

from collect.client import ask
from collect.config import settings

HELP = ("Befehle: /status  /review  /facts  /help  /quit — "
        "alles andere geht als Frage an den Stack.")


def _open_store():
    from ossifikat.store import OssifikatStore
    db = Path(settings.ossifikat_db)
    db.parent.mkdir(parents=True, exist_ok=True)
    return OssifikatStore(str(db))


def cmd_status() -> str:
    from collect.status import get_agent_status
    status = get_agent_status()
    if not status:
        return "⚠️ Keine Heartbeats — läuft collect-agents?"
    lines = []
    for name in sorted(status):
        s = status[name]
        icon = "🟢" if s["alive"] else "🔴"
        lines.append(f"  {icon} {name:<16} (vor {s['age_s']:.0f}s)")
    return "\n".join(lines)


def cmd_facts() -> str:
    store = _open_store()
    try:
        rows = store.query()
    finally:
        store.close()
    if not rows:
        return "Keine verbürgten Fakten."
    return "\n".join(f"  🔖 [{t.id}] {t.subject} —[{t.predicate}]→ {t.object}"
                     for t in rows)


def cmd_review(input_fn=input, print_fn=print) -> str:
    """Staging-Tripel einzeln: [j]a verbürgen · [n]ein verwerfen · [s]kip · [q]."""
    store = _open_store()
    confirmed = rejected = 0
    try:
        staging = store.list_staging()
        if not staging:
            return "Staging ist leer."
        print_fn(f"{len(staging)} Tripel im Staging:")
        for t in staging:
            print_fn(f"\n  [{t.id}] {t.subject} —[{t.predicate}]→ {t.object}"
                     f"\n      (Quelle: {t.source}, Konfidenz {t.confidence:.1f})")
            choice = input_fn("  verbürgen? [j/n/s/q] ").strip().lower()
            if choice == "q":
                break
            if choice == "j":
                store.confirm(t.id, confirmed_by="repl")
                confirmed += 1
            elif choice == "n":
                store.reject(t.id)
                rejected += 1
    finally:
        store.close()
    return f"✓ {confirmed} verbürgt, {rejected} verworfen."


def repl(input_fn=input, print_fn=print) -> int:
    print_fn(f"collect2 REPL — {HELP}")
    while True:
        try:
            line = input_fn("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print_fn("")
            return 0
        if not line:
            continue
        if line in ("/quit", "/q", "q"):
            return 0
        if line in ("/help", "/h"):
            print_fn(HELP)
        elif line == "/status":
            print_fn(cmd_status())
        elif line == "/facts":
            print_fn(cmd_facts())
        elif line == "/review":
            print_fn(cmd_review(input_fn, print_fn))
        elif line.startswith("/"):
            print_fn(f"Unbekannter Befehl: {line} — {HELP}")
        else:
            result = ask(line, show_progress=True)
            print_fn("\n" + result.get("text", ""))
            meta = result.get("meta", {})
            if meta and not meta.get("timeout"):
                print_fn(f"\n[zone={meta.get('zone')} "
                         f"distance={meta.get('best_distance', 0):.1f} "
                         f"dauer={meta.get('duration_s')}s]")


def main() -> int:
    try:
        import readline  # noqa: F401 — Zeilen-Editing/History im input()
    except ImportError:
        pass
    return repl()


if __name__ == "__main__":
    sys.exit(main())
