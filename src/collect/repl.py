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
    > /session save <name>      Session speichern
    > /session list             Sessions auflisten
    > /session load <id>        Session laden
    > /session delete <id>      Session löschen
    > /quit
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from collect.client import ask
from collect.config import settings

HELP = ("Befehle: /status  /review  /facts  /session  /retrieval  /help  /quit — "
        "alles andere geht als Frage an den Stack. "
        "Prefix [profil] forced den Retrieval-Modus (z.B. [precise] Was ist X?).")


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


def _store():
    from collect.session import SessionStore
    return SessionStore()


def cmd_session(args: str, context: dict, print_fn=print) -> str:
    from collect.agents.session import MeetingProtokoll

    parts = args.split()
    sub = parts[0] if parts else ""
    rest = " ".join(parts[1:])

    store = _store()

    if sub == "save":
        sid = context.get("session_id") or str(uuid.uuid4())[:8]
        title = rest or f"Session {sid}"
        store.create(sid, title)
        n = store.turn_count(sid)
        context["session_id"] = sid
        return f"✓ Session '{title}' gespeichert ({n} Turns)."

    if sub == "list":
        sessions = store.list_sessions()
        if not sessions:
            return "Keine Sessions gespeichert."
        lines = []
        for s in sessions:
            lines.append(f"  [{s['id'][:8]}] {s['title'][:40]} "
                         f"({s['turn_count']} Turns, {time.ctime(s['updated_at'])[:16]})")
        return "\n".join(lines)

    if sub == "load":
        if not rest:
            return "Usage: /session load <id>"
        s = store.get(rest[:64])
        if not s:
            return f"Session {rest[:8]} nicht gefunden."
        turns = s.get("turns", [])
        context["session_id"] = s["id"]
        context["history"] = []
        for t in turns[-10:]:
            context["history"].append({"q": t["query"], "a": t["answer"]})
        # Summary als Kontext-Präfix, falls vorhanden
        summary = store.get_summary(s["id"])
        if summary:
            context["history"].insert(0, {"q": "_summary", "a": summary})
        return f"✓ Session '{s['title']}' geladen ({len(turns)} Turns)."

    if sub == "delete":
        if not rest:
            return "Usage: /session delete <id>"
        store.delete(rest[:64])
        if context.get("session_id") == rest[:64]:
            context["session_id"] = None
        return f"Session {rest[:8]} gelöscht."

    if sub == "summarize":
        sid = context.get("session_id")
        if not sid:
            return "Keine aktive Session — zuerst /session save."
        mp = MeetingProtokoll(store)
        summary = mp.summarize(sid)
        return f"✓ Summarized ({len(summary)} Zeichen)." if summary else "✗ Summary fehlgeschlagen."

    import time as _time  # noqa: F811
    return ("/session save [name]  /session list  /session load <id>  "
            "/session delete <id>  /session summarize")


def cmd_retrieval(args: str, context: dict) -> str:
    """Zeigt/setzt das Retrieval-Profil dieser REPL-Session.

    Das Retrieval läuft im SERVICE-Prozess — Settings hier im Client zu
    mutieren wäre wirkungslos. Stattdessen merkt sich die REPL das Profil
    und sendet es als [profil]-Prefix mit jeder Frage (derselbe Mechanismus
    wie der Profil-Selector der Web-UI)."""
    from collect.config import settings

    profiles = list(settings.retrieval_profiles.keys())
    active = context.get("profile") or "auto"
    if not args:
        return (f"Session-Profil: {active}\n"
                f"Verfügbar: {', '.join(profiles)}, auto\n"
                f"Setzen: /retrieval set <profil> — Einmalig: [profil] Frage?")

    parts = args.split()
    sub = parts[0]

    if sub == "set" and len(parts) > 1:
        name = parts[1].lower()
        if name == "auto":
            context.pop("profile", None)
            return "✓ Session-Profil zurück auf 'auto' (Service erkennt selbst)."
        if name in profiles:
            context["profile"] = name
            return (f"✓ Session-Profil '{name}' — wird als [{name}]-Prefix "
                    f"mit jeder Frage gesendet.")
        return f"✗ Unbekanntes Profil: {name}. Verfügbar: {', '.join(profiles)}, auto"

    if sub == "test":
        rest = " ".join(parts[1:]) or "Was ist Quantencomputing?"
        from collect.retrieval.profiles import auto_detect
        return f"Query: {rest[:60]}\nAuto-Detect → {auto_detect(rest)}"

    return f"/retrieval [set <profil>] [test <query>] — Profile: {', '.join(profiles)}"


def repl(input_fn=input, print_fn=print) -> int:
    print_fn(f"{settings.display_name} REPL — {HELP}")
    _session_store = _store()
    context: dict = {"history": [], "session_id": None}
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
        elif line.startswith("/session"):
            args = line[len("/session"):].strip()
            print_fn(cmd_session(args, context, print_fn))
        elif line.startswith("/retrieval"):
            args = line[len("/retrieval"):].strip()
            print_fn(cmd_retrieval(args, context))
        elif line.startswith("/"):
            print_fn(f"Unbekannter Befehl: {line} — {HELP}")
        else:
            sid = context.get("session_id")
            # Session-Profil als Prefix (Service strippt ihn im Orchestrator)
            outgoing = line
            if context.get("profile") and not line.startswith("["):
                outgoing = f"[{context['profile']}] {line}"
            result = ask(outgoing, show_progress=True,
                         history=list(context["history"]), session_id=sid)
            print_fn("\n" + result.get("text", ""))
            meta = result.get("meta", {})
            if meta and not meta.get("timeout"):
                context["history"].append({"q": line, "a": result.get("text", "")})
                del context["history"][:-settings.session_max_turns]
                if sid:
                    _session_store.add_turn(
                        sid, line, result.get("text", ""),
                        zone=meta.get("zone", ""), best_distance=meta.get("best_distance"),
                        duration_s=meta.get("duration_s"), meta=meta)
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
