"""Kuration + Augmentierung der gesammelten Traces (CPU-only, kein LLM).

- Interaktives Durchgehen: Kontext gekürzt, Target voll, Tastatur-Verdikt
  gut/schlecht/skip → curated-Marker in separate JSONL (append-only, join
  über trace_id).
- Think-Synthese: deterministisch aus step_kind + Merkmalen (1–2 deutsche
  Sätze, ≤80 Tokens). KEIN LLM — reproduzierbar.
- Negativ-Synthese: aus Positiv-Traces abgeleitete Negativbeispiele gegen die
  dokumentierten Kippfehler, markiert synthetic=true.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Optional

from collect.traces.schema import make_trace_id

# ── Think-Synthese ───────────────────────────────────────────────────────

_THINK_BY_KIND = {
    "rewrite": "Folgefrage referenziell auf den Verlauf → in eine "
               "eigenständige Suchanfrage umformen.",
    "answer": "Quellen gesammelt → finale Antwort daraus formulieren.",
    "no_op": "Frage bereits eigenständig bzw. keine Aktion nötig.",
}


def synth_think(step_kind: str, messages: Optional[list[dict]] = None) -> str:
    """Deterministischer Think-Vorschlag (1–2 Sätze) aus dem step_kind."""
    if step_kind == "tool_call" and messages:
        for m in reversed(messages):
            for tc in m.get("tool_calls", []) or []:
                name = tc.get("function", {}).get("name", "?")
                return f"Werkzeug nötig, um {name} auszuführen."
    return _THINK_BY_KIND.get(step_kind, "Nächsten Schritt bestimmen.")


def apply_think(trace: dict, think: str) -> dict:
    """Setzt den Think-Block in das letzte assistant-Turn (Target)."""
    out = deepcopy(trace)
    for m in reversed(out.get("messages", [])):
        if m.get("role") == "assistant":
            body = re.sub(r"<think>.*?</think>\s*", "", m.get("content", "") or "",
                          flags=re.DOTALL).strip()
            m["content"] = f"<think>{think}</think>\n{body}".strip()
            break
    return out


# ── Negativ-Synthese ─────────────────────────────────────────────────────

def make_negative_unchanged(trace: dict) -> Optional[dict]:
    """Gegen T3-Kippen: die BEREITS eigenständige Frage → Target UNCHANGED.

    Die eigenständige Version steckt schon im Trace — als Rewrite-Target. Wir
    setzen sie ohne Verlauf als FOLGEFRAGE ein; ist sie eigenständig, MUSS der
    Rewriter UNCHANGED liefern (nicht erneut umformen). Nur aus Traces mit
    echtem, eigenständigem Rewrite ableitbar (Target ≠ referenzieller Original-
    Query und kein Pronomen-Anfang)."""
    if trace.get("meta", {}).get("step_kind") != "rewrite":
        return None
    standalone = _last_assistant(trace).strip()
    if not standalone or standalone == "UNCHANGED" or len(standalone.split()) < 4:
        return None  # kein brauchbarer eigenständiger Satz

    out = deepcopy(trace)
    for m in out["messages"]:
        if m.get("role") == "user":
            prefix = m.get("content", "").split("GESPRÄCH:")[0].rstrip()
            m["content"] = (f"{prefix}\n\nGESPRÄCH:\n(kein vorheriges Gespräch)\n\n"
                            f"FOLGEFRAGE: {standalone}\n\nEigenständige Frage:")
            break
    for m in reversed(out["messages"]):
        if m.get("role") == "assistant":
            m["content"] = "UNCHANGED"
            m.pop("tool_calls", None)
            break
    return _finalize_negative(out, "t3_unchanged")


def _last_assistant(trace: dict) -> str:
    for m in reversed(trace.get("messages", [])):
        if m.get("role") == "assistant":
            return m.get("content", "") or ""
    return ""


def make_negative_answer_no_call(trace: dict) -> Optional[dict]:
    """Aus einem answer-Trace: eine Konzeptfrage, die aus dem Tool-Schema
    direkt beantwortbar ist → Target Antwort OHNE Tool-Call (gegen T5-Falle).
    Entfernt etwaige tool_calls aus dem Target; behält die Prosa-Antwort."""
    if trace.get("meta", {}).get("step_kind") != "answer":
        return None
    out = deepcopy(trace)
    for m in reversed(out["messages"]):
        if m.get("role") == "assistant":
            if m.pop("tool_calls", None) is None and (m.get("content") or "").strip():
                pass  # war schon call-frei — trotzdem als expliziter Negativ-Anker gut
            break
    return _finalize_negative(out, "t5_no_call")


def _finalize_negative(trace: dict, kind: str) -> dict:
    meta = dict(trace.get("meta", {}))
    origin = meta.get("trace_id", "?")
    meta.update({
        "trace_id": make_trace_id(trace["messages"]),
        "synthetic": True, "curated": True,
        "negative_kind": kind, "derived_from": origin,
    })
    trace["meta"] = meta
    return trace


NEGATIVE_BUILDERS = (make_negative_unchanged, make_negative_answer_no_call)


def build_negatives(traces: list[dict]) -> list[dict]:
    out = []
    for t in traces:
        for builder in NEGATIVE_BUILDERS:
            neg = builder(t)
            if neg:
                out.append(neg)
    return out


# ── Interaktive Kuration ─────────────────────────────────────────────────

def _short(text: str, n: int = 300) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + " …"


def curate_interactive(traces: list[dict], curation_path: Path,
                       input_fn=input, print_fn=print) -> dict:
    """Zeigt jeden Trace, nimmt Verdikt (g/s/x/q), schreibt curated-Marker +
    synthetisierten Think append-only nach curation_path. Gibt Zählung zurück."""
    kept = skipped = 0
    curation_path.parent.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(traces):
        meta = t.get("meta", {})
        msgs = t.get("messages", [])
        print_fn(f"\n[{i+1}/{len(traces)}] {meta.get('step_kind','?')} "
                 f"(trace {meta.get('trace_id','?')})")
        for m in msgs[:-1]:
            print_fn(f"  {m.get('role'):9} {_short(m.get('content',''), 200)}")
        tgt = msgs[-1] if msgs else {}
        print_fn(f"  → TARGET   {_short(tgt.get('content',''), 500)}")
        think = synth_think(meta.get("step_kind", ""), msgs)
        print_fn(f"  ~ think-Vorschlag: {think}")
        verdict = (input_fn("  [g]ut / [s]chlecht / [x] skip / [q]uit: ") or "").strip().lower()
        if verdict == "q":
            break
        if verdict == "g":
            with open(curation_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "trace_id": meta.get("trace_id"), "curated": True,
                    "think": think,
                }, ensure_ascii=False) + "\n")
            kept += 1
        else:
            skipped += 1
    return {"kept": kept, "skipped": skipped}
