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

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Optional

from collect.traces.schema import make_trace_id

# ── Think-Synthese ───────────────────────────────────────────────────────

# Mehrere Formulierungen pro step_kind: ein WORTGLEICHER Think in jedem
# Beispiel macht den Großteil der Target-Tokens trivial vorhersagbar — die Loss
# fällt dann, ohne dass die Fähigkeit besser wird (im 3B-Mechanik-Lauf war die
# Loss in Epoche 1 bei 0,0001). Die Auswahl bleibt deterministisch: stabiler
# Hash über die trace_id, kein Zufall, kein LLM.
_THINK_VARIANTS = {
    "rewrite": [
        "Folgefrage referenziell auf den Verlauf → in eine eigenständige "
        "Suchanfrage umformen.",
        "Die Frage zeigt auf den Verlauf zurück; das Thema einsetzen, damit "
        "sie eigenständige Bedeutung hat.",
        "Ohne den Verlauf ist die Frage unverständlich → Antezedent auflösen "
        "und eigenständige Suchanfrage bilden.",
        "Bezug aus dem Gespräch übernehmen und als eigenständige Frage "
        "ausformulieren.",
        "Referenz auflösen: das gemeinte Thema benennen, Rest der Frage "
        "erhalten — Ergebnis muss eigenständige Suchanfrage sein.",
    ],
    "answer": [
        "Quellen gesammelt → finale Antwort daraus formulieren.",
        "Belege liegen vor; daraus eine knappe Antwort schreiben.",
        "Genug Material zusammen — Antwort aus den Fundstellen ableiten.",
    ],
    "no_op": [
        "Frage bereits eigenständig bzw. keine Aktion nötig.",
        "Nichts zu tun: die Anfrage steht schon für sich.",
    ],
}


def synth_think(step_kind: str, messages: Optional[list[dict]] = None,
                trace_id: str = "") -> str:
    """Deterministischer Think-Vorschlag (1–2 Sätze) aus step_kind + trace_id.

    Gleiche trace_id ⇒ gleicher Think (reproduzierbar), verschiedene Traces ⇒
    unterschiedliche Formulierung (kein auswendig lernbares Präfix)."""
    if step_kind == "tool_call" and messages:
        for m in reversed(messages):
            for tc in m.get("tool_calls", []) or []:
                name = tc.get("function", {}).get("name", "?")
                return f"Werkzeug nötig, um {name} auszuführen."
    variants = _THINK_VARIANTS.get(step_kind)
    if not variants:
        return "Nächsten Schritt bestimmen."
    return variants[_stable_index(trace_id, len(variants))]


def _stable_index(key: str, n: int) -> int:
    """Stabil über Prozess-Neustarts hinweg (hash() ist es nicht)."""
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % n


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


# ── Vor-Filter: mechanische Ablehnungsregeln (docs/traces_curation.md) ────
# "auto"-Hälfte der Rubrik: entscheidbar ohne Urteil → lehnt vorab ab, damit
# der Mensch nur die semantischen Grenzfälle (Antezedent, zerhackter Begriff)
# sieht. Reine Reject-Regeln — Annehmen bleibt Urteilssache (Antezedent-Erhalt
# lässt sich nicht mechanisch verifizieren).
_LEADING_CONJ = re.compile(r"^(und|aber|oder|also)\b", re.IGNORECASE)
_SIE = re.compile(r"\b(Sie|Ihnen|Ihre|Ihrer|Ihren|Ihrem|Ihres)\b")


def _follow_up(trace: dict) -> str:
    for m in trace.get("messages", []):
        if m.get("role") == "user":
            mm = re.search(r"FOLGEFRAGE:\s*(.+?)(?:\n|$)", m.get("content", ""))
            if mm:
                return mm.group(1).strip()
    return ""


def prefilter(trace: dict) -> Optional[str]:
    """Mechanischer Ablehnungsgrund oder None (besteht → Mensch entscheidet).

    Greift nur bei step_kind=rewrite; andere Kategorien werden durchgereicht."""
    if trace.get("meta", {}).get("step_kind") != "rewrite":
        return None
    target = re.sub(r"<think>.*?</think>", "", _last_assistant(trace),
                    flags=re.DOTALL).strip()
    if not target:
        return "leeres Target"
    follow = _follow_up(trace)
    if follow and target == follow:
        return "kein Rewrite (Passthrough)"
    if _LEADING_CONJ.match(target):
        return "Konjunktions-Anfang (und/aber/oder/also)"
    if _SIE.search(target):
        return "Sie-Register"
    if len(target.split()) < 4:
        return "zu kurz (<4 Wörter)"
    return None


# ── Interaktive Kuration ─────────────────────────────────────────────────

def _short(text: str, n: int = 300) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + " …"


def curate_interactive(traces: list[dict], curation_path: Path,
                       input_fn=input, print_fn=print,
                       use_prefilter: bool = True) -> dict:
    """Zeigt jeden Trace, nimmt Verdikt (g/s/x/q), schreibt curated-Marker +
    synthetisierten Think append-only nach curation_path. Gibt Zählung zurück.

    use_prefilter=True: mechanisch ablehnbare Traces werden vorab aussortiert
    (Regel-Verweis geloggt), nur die Urteils-Fälle werden vorgelegt."""
    kept = skipped = auto = 0
    auto_rejected: list[dict] = []
    curation_path.parent.mkdir(parents=True, exist_ok=True)
    survivors = []
    for t in traces:
        reason = prefilter(t) if use_prefilter else None
        if reason:
            auto += 1
            auto_rejected.append({"trace_id": t.get("meta", {}).get("trace_id"),
                                  "reason": reason})
            print_fn(f"  [auto-reject] {t.get('meta',{}).get('trace_id','?')}: {reason}")
        else:
            survivors.append(t)
    if auto:
        print_fn(f"\n{auto} mechanisch abgelehnt (Rubrik). "
                 f"{len(survivors)} zur Sichtung:\n")
    for i, t in enumerate(survivors):
        meta = t.get("meta", {})
        msgs = t.get("messages", [])
        print_fn(f"\n[{i+1}/{len(survivors)}] {meta.get('step_kind','?')} "
                 f"(trace {meta.get('trace_id','?')})")
        for m in msgs[:-1]:
            print_fn(f"  {m.get('role'):9} {_short(m.get('content',''), 200)}")
        tgt = msgs[-1] if msgs else {}
        print_fn(f"  → TARGET   {_short(tgt.get('content',''), 500)}")
        think = synth_think(meta.get("step_kind", ""), msgs, meta.get("trace_id", ""))
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
    return {"kept": kept, "skipped": skipped,
            "auto_rejected": auto, "auto_reasons": auto_rejected}


# ── Trainingssatz bauen (reproduzierbar) ─────────────────────────────────

def load_curated_ids(curation_path: Path) -> set[str]:
    """trace_ids mit positivem Verdikt aus der append-only Kurations-JSONL.
    Späterer Eintrag gewinnt (Override durch erneutes Kurieren)."""
    verdicts: dict[str, bool] = {}
    if not curation_path.exists():
        return set()
    for line in curation_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("trace_id"):
            verdicts[d["trace_id"]] = bool(d.get("curated"))
    return {tid for tid, ok in verdicts.items() if ok}


def build_training_set(traces: list[dict], curated_ids: set[str],
                       eval_n: int = 10,
                       negative_ratio: float = 0.3) -> dict:
    """Baut Trainings- und Eval-Satz aus den kuratierten Positiven.

    - Reihenfolge deterministisch über trace_id (kein Zufall ⇒ derselbe
      Trace-Bestand ergibt denselben Split).
    - Eval-Split per Stride, damit die zurückgehaltenen Beispiele thematisch
      gestreut sind statt „die letzten zehn".
    - Negative NUR aus Train-Positiven — ein Negativ aus einem Eval-Trace
      würde dessen Inhalt ins Training lecken.
    - Think: variiert pro Trace im Train-Satz; im Eval-Satz KEIN Think, dort
      wird gegen die reine Zielfrage verglichen.
    """
    positives = sorted((t for t in traces
                        if t.get("meta", {}).get("trace_id") in curated_ids),
                       key=lambda t: t["meta"]["trace_id"])
    eval_n = max(0, min(eval_n, len(positives) // 2))
    eval_idx: set[int] = set()
    if eval_n:
        stride = len(positives) / eval_n
        eval_idx = {min(int(i * stride), len(positives) - 1) for i in range(eval_n)}
    eval_set = [positives[i] for i in sorted(eval_idx)]
    train_pos = [t for i, t in enumerate(positives) if i not in eval_idx]

    negatives = build_negatives(train_pos)
    if negative_ratio > 0 and train_pos:
        cap = int(len(train_pos) * negative_ratio / (1 - negative_ratio)) or 1
    else:
        cap = 0
    if len(negatives) > cap:  # per Stride ausdünnen statt vorne abschneiden
        step = len(negatives) / cap if cap else 0
        negatives = [negatives[min(int(i * step), len(negatives) - 1)]
                     for i in range(cap)] if cap else []

    train = [_with_think(t) for t in train_pos + negatives]
    return {"train": train, "eval": [_strip_think(t) for t in eval_set],
            "n_positive": len(train_pos), "n_negative": len(negatives)}


def _with_think(trace: dict) -> dict:
    meta = trace.get("meta", {})
    return apply_think(trace, synth_think(meta.get("step_kind", ""),
                                          trace.get("messages"),
                                          meta.get("trace_id", "")))


def _strip_think(trace: dict) -> dict:
    out = deepcopy(trace)
    for m in reversed(out.get("messages", [])):
        if m.get("role") == "assistant":
            m["content"] = re.sub(r"<think>.*?</think>\s*", "",
                                  m.get("content", "") or "",
                                  flags=re.DOTALL).strip()
            break
    return out
