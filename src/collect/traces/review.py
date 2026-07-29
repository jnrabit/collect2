"""Review-Modus — geführtes Sichten der Traces für die Fehler-Inventur.

Transport-Schicht: Terminal-Ausgabe und Tastatur. Die Logik (Flags schreiben,
aufheben, aggregieren) liegt in `flags.py`.

Bewusst ein CLI-Werkzeug, keine TUI: Rohtext, keine neuen Dependencies.
Fortschritt ist automatisch persistent, weil `review` nur ungesichtete Traces
vorlegt und jede Entscheidung sofort append-only landet — ein Abbruch verliert
nichts, ein Neustart setzt an der ersten ungesichteten Stelle fort.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from collect.traces.flags import (
    FEHLERKLASSEN, NONE, ungesichtete, write_flag,
)

_CTX_MAX = 240      # Kontext wird gekürzt — das Target zählt
_SEP = "─" * 72


def _kuerzen(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _tool_calls(msg: dict) -> str:
    calls = msg.get("tool_calls") or []
    if not calls:
        return ""
    out = []
    for c in calls:
        fn = c.get("function", c)
        name = fn.get("name", "?")
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass
        out.append(f"    → {name}({json.dumps(args, ensure_ascii=False)})")
    return "\n".join(out)


def render_trace(trace: dict, index: int = 0, total: int = 0) -> str:
    """Kompakte Darstellung: Kontext gekürzt, Target voll, Tool-Calls formatiert."""
    meta = trace.get("meta", {})
    msgs = trace.get("messages", [])
    kopf = (f"[{index}/{total}] {meta.get('trace_id', '?')}  "
            f"kind={meta.get('step_kind', '?')}  {meta.get('timestamp', '')}")
    zeilen = [_SEP, kopf, _SEP]
    for m in msgs[:-1]:
        rolle = m.get("role", "?")
        inhalt = _kuerzen(m.get("content", ""), _CTX_MAX)
        zeilen.append(f"  {rolle:9} {inhalt}")
        tc = _tool_calls(m)
        if tc:
            zeilen.append(tc)
    if msgs:
        ziel = msgs[-1]
        zeilen.append("")
        zeilen.append("  TARGET (assistant):")
        inhalt = (ziel.get("content") or "").strip()
        for zeile in (inhalt.splitlines() or [""]):
            zeilen.append(f"    {zeile}")
        tc = _tool_calls(ziel)
        if tc:
            zeilen.append(tc)
    return "\n".join(zeilen)


def _legende(klassen: list[str]) -> str:
    teile = [f"{i+1}={k}" for i, k in enumerate(klassen)]
    return ("  Enter=korrekt   " + "  ".join(teile) + "   s=skip  q=Ende")


def review_loop(traces: list[dict], flags: list[dict],
                base_dir: Optional[Path] = None,
                flagger: str = "", eingabe=input, ausgabe=print) -> dict:
    """Geht ungesichtete Traces durch. Gibt Zählung zurück.

    `flagger` wird in jede Zeile geschrieben — wer geurteilt hat, muss später
    trennbar sein (Mensch vs. Assistent), sonst ist die Inventur nicht
    auditierbar.

    `eingabe`/`ausgabe` sind injizierbar, damit der Loop testbar bleibt, ohne
    ein Terminal zu brauchen.
    """
    offen = ungesichtete(traces, flags)
    klassen = list(FEHLERKLASSEN)
    zaehlung = {"gesichtet": 0, "korrekt": 0, "geflaggt": 0, "skip": 0}
    if not offen:
        ausgabe("Nichts zu sichten — alle Traces sind bereits gesichtet.")
        return zaehlung

    ausgabe(f"{len(offen)} ungesichtete Traces. Rubrik: docs/traces_fehlerklassen.md")
    for i, trace in enumerate(offen, 1):
        ausgabe(render_trace(trace, i, len(offen)))
        ausgabe(_legende(klassen))
        tid = trace.get("meta", {}).get("trace_id", "")
        while True:
            try:
                wahl = eingabe("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ausgabe("\nAbbruch — bereits Gesichtetes ist gespeichert.")
                return zaehlung
            if wahl == "q":
                ausgabe("Ende. Fortschritt gespeichert.")
                return zaehlung
            if wahl == "s":
                zaehlung["skip"] += 1
                break
            if wahl == "":
                write_flag(tid, NONE, note="review: korrekt", base_dir=base_dir,
                           flagger=flagger)
                zaehlung["gesichtet"] += 1
                zaehlung["korrekt"] += 1
                break
            klasse = None
            if wahl.isdigit() and 1 <= int(wahl) <= len(klassen):
                klasse = klassen[int(wahl) - 1]
            elif wahl in FEHLERKLASSEN:
                klasse = wahl
            if not klasse:
                ausgabe("  ungültig — Ziffer, Klassenname, Enter, s oder q")
                continue
            note = ""
            if klasse == "sonstiges":
                note = eingabe("  Notiz (Pflicht): ").strip()
                if not note:
                    ausgabe("  'sonstiges' ohne Notiz ist wertlos — bitte Notiz angeben")
                    continue
            write_flag(tid, klasse, note=note, base_dir=base_dir, flagger=flagger)
            zaehlung["gesichtet"] += 1
            zaehlung["geflaggt"] += 1
            break
    ausgabe("Alle vorgelegten Traces durch.")
    return zaehlung
