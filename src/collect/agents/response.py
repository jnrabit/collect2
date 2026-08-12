"""ResponseAgent — sammelt Beiträge laut Manifest und synthetisiert die Antwort.

Finalisierung ist EXPLIZIT (DESIGN.md §5.5): das Manifest des Orchestrators
sagt, welche Beiträge kommen; finalisiert wird bei Vollständigkeit oder
Deadline (bus.call_later) — die Timeout-Heuristiken des Alt-Systems entfallen.

Synthese (Reihenfolge aus der Stabilisierung des Alt-Systems):
  1. Plan-Sektion zuerst (falls planning-Beitrag)
  2. LLM-Antwort — mit Grauzonen-Hinweis, oder Hard-Fallback-Text wenn die
     Zone FALLBACK ist (Halluzinations-Schutz; Plan-Antworten werden nie
     unterdrückt)
  3. Quellen-Fußzeile (Trefferzahl, beste Distance, Zone)
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.config import settings
from collect.retrieval.zones import (
    NO_HIT_DISTANCE,
    ZONE_FALLBACK,
    ZONE_GRAY,
    classify_zone,
    fallback_suppressed,
)

CONTRIB_CHANNELS = {
    "retrieval_response": "retrieval",
    "code_retrieval_response": "code_retrieval",
    "llm_response": "llm",
    "llm_response_deepseek": "llm_deepseek",
    "planning_response": "planning",
    "workflow_response": "workflow",
    "file_response": "file",
    "web_response": "web",
}


class ResponseAgent(BaseAgent):
    name = "response"

    def __init__(self, bus):
        super().__init__(bus)
        self._states: dict[str, dict] = {}  # cid → {expected, contribs, …}

    def subscriptions(self):
        subs = {"response_manifest": self.on_manifest}
        for channel in CONTRIB_CHANNELS:
            subs[channel] = self.on_contribution
        return subs

    # ── Manifest + Sammeln ───────────────────────────────────────────────

    def on_manifest(self, msg: Message) -> None:
        cid = msg.correlation_id
        deadline = float(msg.data.get("deadline", 120.0))
        self._states[cid] = {
            "query": msg.data.get("query", ""),
            "rewritten_query": msg.data.get("rewritten_query"),
            "expected": set(msg.data.get("expected", [])),
            "contribs": {},
            "reply_to": msg.reply_to or "user_response",
            "finalized": False,
            "started_at": time.time(),
        }
        self.log.info("%s: Manifest %s (Deadline %.0fs)",
                      cid[:8], sorted(self._states[cid]["expected"]), deadline)
        self.bus.call_later(deadline, lambda: self._deadline(cid))

    def on_contribution(self, msg: Message) -> None:
        state = self._states.get(msg.correlation_id)
        if state is None or state["finalized"]:
            return
        kind = CONTRIB_CHANNELS.get(msg.type)
        if kind is None or kind in state["contribs"]:
            return  # unbekannt oder Duplikat
        state["contribs"][kind] = msg.data
        have = len(set(state["contribs"]) & state["expected"])
        self.log.info("%s: Beitrag %s (%d/%d)", msg.correlation_id[:8], kind,
                      have, len(state["expected"]))
        if state["expected"] <= set(state["contribs"]):
            self._finalize(msg.correlation_id, reason="complete")

    def _deadline(self, cid: str) -> None:
        state = self._states.get(cid)
        if state is None or state["finalized"]:
            return
        missing = state["expected"] - set(state["contribs"])
        self.log.warning("%s: Deadline — fehlend: %s", cid[:8], sorted(missing))
        self._finalize(cid, reason="deadline")

    # ── Synthese ─────────────────────────────────────────────────────────

    def _finalize(self, cid: str, reason: str) -> None:
        state = self._states.pop(cid, None)
        if state is None or state["finalized"]:
            return
        state["finalized"] = True

        # Auto-Web wird jetzt vom LLMAgent VOR der Generierung ausgelöst (er
        # kennt die Zone dort schon und synthetisiert die Web-Treffer mit) —
        # kein Nachforder-Trigger mehr hier. Die web-Contribution landet
        # trotzdem im state (für den 🌐-Footer).

        text, meta = synthesize(state)
        meta["finalize_reason"] = reason
        meta["duration_s"] = round(time.time() - state["started_at"], 2)

        self.publish(state["reply_to"], "user_response",
                     {"text": text, "meta": meta}, cid)

        # Lern-Event für den LearningAgent (Triplet-Log + Fakt-Staging) —
        # asynchron zum Antwort-Pfad, der Nutzer wartet nie aufs Lernen.
        context_ids = []
        for kind in ("retrieval", "code_retrieval"):
            contrib = state["contribs"].get(kind) or {}
            context_ids.extend(h.get("doc_id") for h in contrib.get("hits", []))
        self.publish("answer_recorded", "answer_recorded", {
            "query": state["query"],
            "text": text,
            "zone": meta.get("zone"),
            "best_distance": meta.get("best_distance"),
            "plan_id": meta.get("plan_id"),
            "context_ids": context_ids,
            # Dateikontext-Antworten sind flüchtig → LearningAgent ossifiziert nicht
            "has_file_context": bool(meta.get("file_chunks")),
        }, cid)
        self.log.info("%s: finalisiert (%s, %.1fs)", cid[:8], reason, meta["duration_s"])


# ── Bloat-Filter: entfernt redundante Sätze (Satz-n im Wesentlichen = Satz-n−1) ──

# Deutsche Stopwörter für Wort-Überlappungs-Berechnung
_STOP = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "und", "oder", "aber", "ist", "sind", "war", "wird", "werden", "wurde",
    "nicht", "mit", "von", "zu", "auf", "in", "aus", "für", "bei", "durch",
    "auch", "noch", "nur", "wie", "was", "wann", "warum", "sich", "dass",
    "kann", "können", "soll", "muss", "hat", "haben", "wenn", "weil", "da",
    "als", "bis", "um", "vor", "nach", "seit", "über", "unter", "zwischen",
    "dann", "noch", "schon", "immer", "jetzt", "hier", "dort", "so", "also",
    "zusammenfassend", "abschließend", "festgehalten", "lässt", "sagen",
    "könnte", "würde", "wäre", "möchte", "mehr", "weniger", "dabei", "dazu",
    "damit", "dadurch", "dafür", "dagegen", "deshalb", "trotzdem", "denn",
})

_SENT_RE = re.compile(r"[^.!?\n]+[.!?\n]+")


def _strip_bloat(text: str) -> str:
    """Sätze mit >50% Wort-Überlappung zum Vorgänger entfernen (Bloat-Filter).
    Fängt generische Füllsätze: 'Zusammenfassend lässt sich sagen, dass der
    Lorenz-Attraktor ein chaotisches System ist. Der Lorenz-Attraktor ist
    also ein chaotisches System, das...' → zweiter Satz fliegt."""
    if not text or len(text) < 80:
        return text
    sentences = [s.strip() for s in _SENT_RE.findall(text) if s.strip()]
    if len(sentences) <= 1:
        return text

    filtered = [sentences[0]]
    for sent in sentences[1:]:
        prev_words = {w.lower() for w in re.findall(r"[a-zA-ZäöüßÄÖÜ]+", filtered[-1])
                      if w.lower() not in _STOP and len(w) > 2}
        cur_words = {w.lower() for w in re.findall(r"[a-zA-ZäöüßÄÖÜ]+", sent)
                     if w.lower() not in _STOP and len(w) > 2}
        if not prev_words or not cur_words:
            filtered.append(sent)
            continue
        overlap = len(prev_words & cur_words) / min(len(prev_words), len(cur_words))
        if overlap <= 0.5:
            filtered.append(sent)
    return " ".join(filtered) if len(filtered) < len(sentences) else text


# ── Synthese ─────────────────────────────────────────────────────────

def synthesize(state: dict) -> tuple[str, dict]:
    """Reine Synthese-Funktion (transportfrei, direkt testbar)."""
    contribs = state["contribs"]
    expected = state["expected"]
    parts: list[str] = []
    meta: dict = {}
    if state.get("rewritten_query"):
        meta["rewritten_query"] = state["rewritten_query"]

    # 1. Plan-/Workflow-Sektion zuerst — werden nie unterdrückt
    planning = contribs.get("planning")
    if planning:
        parts.append(planning.get("message", ""))
        meta["plan_id"] = planning.get("plan_id")
        meta["executed"] = planning.get("executed")
    workflow = contribs.get("workflow")
    if workflow:
        parts.append(workflow.get("report", ""))
        meta["workflow_ok"] = workflow.get("ok")
        meta["committed"] = workflow.get("committed")

    # Ad-hoc-Dateikontext: geerdet (kein Zonen-Verdikt) bzw. Ablehnung
    file_contrib = contribs.get("file")
    has_file = bool(file_contrib and file_contrib.get("chunks"))
    if file_contrib:
        meta["file_paths"] = file_contrib.get("paths", [])
        meta["file_chunks"] = file_contrib.get("chunk_count", 0)
        rejected = file_contrib.get("rejected") or []
        if rejected and not has_file:
            parts.append("🚫 Pfad nicht freigegeben: " + ", ".join(rejected)
                         + "\n(Freigabe über COLLECT_READ_PATHS — bewusster Opt-in.)")
            meta["file_rejected"] = rejected

    # Zonen-Lage über die Retrieval-Beiträge
    retrieval = contribs.get("retrieval")
    code = contribs.get("code_retrieval")
    best = min((c.get("best_distance", NO_HIT_DISTANCE)
                for c in (retrieval, code) if c), default=NO_HIT_DISTANCE)
    verdict = classify_zone(best if best < NO_HIT_DISTANCE else None)
    meta["zone"] = verdict.zone
    meta["best_distance"] = verdict.best_distance

    # 2. LLM-Antwort mit Drei-Zonen-Logik. Verbürgte Fakten heben den
    # Hard-Fallback auf: eine von Fakten geerdete Antwort wird nicht unterdrückt.
    # Bei Multi-Model-Ensemble: alle Antworten gegeneinander bewerten
    # (vibelike-inspiriertes Konsens-Scoring: 40 % Overlap + 40 % Capability
    # + 20 % Effort, mit Gap-Detection).
    llm_keys = [k for k in contribs if k.startswith("llm")]
    is_ensemble = len(llm_keys) > 1

    if is_ensemble:
        llm_content, llm_contrib, ensemble_meta = _ensemble_llm_content(
            contribs, llm_keys, verdict)
        meta.update(ensemble_meta)
    else:
        llm_contrib = contribs.get("llm")
        llm_content = None
        single = _build_llm_answer_entry("llm", llm_contrib, True, None)
        meta["llm_answers"] = [single]

    facts_used = int(llm_contrib.get("facts_used", 0)) if llm_contrib else 0
    meta["facts_used"] = facts_used
    if llm_contrib and llm_contrib.get("eval_count"):
        meta["tokens"] = llm_contrib["eval_count"]
        meta["tok_per_s"] = llm_contrib.get("tok_per_s")
    if llm_contrib is not None:
        content = _strip_bloat((llm_content or llm_contrib.get("content") or "").strip())
        # Erdung wie in llm.py; Entscheidung zentral in zones.fallback_suppressed
        grounded = bool(planning or workflow or has_file
                        or (facts_used and content))
        if fallback_suppressed(verdict.zone == ZONE_FALLBACK, grounded):
            parts.append(
                "⚠️ Diese Frage liegt außerhalb des indizierten Wissensbereichs. "
                "Der Vault enthält keine ausreichend nahen Dokumente "
                f"(beste Distance: {verdict.best_distance:.1f}, "
                f"Schwellwert: {verdict.soft_max_distance:.0f}).")
        elif content:
            if verdict.zone == ZONE_FALLBACK and not grounded:
                # Schutz per COLLECT_FALLBACK_SUPPRESS=false abgeschaltet:
                # Antwort zeigen, aber unmissverständlich kennzeichnen.
                parts.append(
                    f"⚠️ *FALLBACK-Zone (beste Distance "
                    f"{verdict.best_distance:.1f} ≥ {verdict.soft_max_distance:.0f}) "
                    "— Antwort ist NICHT vault-geerdet (Halluzinations-Schutz "
                    "deaktiviert).*")
            elif verdict.zone == ZONE_GRAY:
                parts.append(
                    f"ℹ️ *Nur entfernte Vault-Treffer (beste Distance "
                    f"{verdict.best_distance:.1f} > {verdict.trust_threshold:.0f}) "
                    "— Antwort mit Vorsicht genießen.*")
            parts.append(content)
            # Ans Token-Limit gelaufen? Ehrlich kennzeichnen statt mitten
            # im Satz stumm zu enden (eval_count == num_predict ⇒ gekappt).
            if (llm_contrib.get("eval_count") or 0) >= settings.llm_num_predict:
                parts.append(
                    "✂️ *Antwort am Token-Limit abgeschnitten "
                    f"({settings.llm_num_predict} Tokens — "
                    "COLLECT_LLM_NUM_PREDICT erhöhen für längere Antworten).*")
        elif llm_contrib.get("error"):
            parts.append(f"⚠️ LLM-Fehler: {llm_contrib['error']}")
    elif "llm" in expected and not planning:
        parts.append("⚠️ Keine LLM-Antwort erhalten (Timeout).")

    # 3. Quellen — strukturiert für Frontend (aufklappbar) + Footer-Text
    source_list: list[dict] = []
    for kind, label in [("retrieval", "general"), ("code_retrieval", "code"), ("web", "web")]:
        contrib = contribs.get(kind)
        if not contrib:
            continue
        for h in contrib.get("hits", [])[:8]:
            source_list.append({
                "doc_id": str(h.get("doc_id", "")),
                "title": str(h.get("title", ""))[:120],
                "source": str(h.get("source", "")),
                "kind": label,
                "distance": round(float(h.get("distance", 0)), 1) if h.get("distance") else None,
            })
    meta["sources"] = source_list

    footer = []
    if has_file:
        paths = file_contrib.get("paths", [])
        shown = ", ".join(Path(p).name for p in paths[:3]) + ("…" if len(paths) > 3 else "")
        footer.append(f"📄 Datei: {shown} ({file_contrib.get('chunk_count', 0)} Chunk(s))")
    web_contrib = contribs.get("web")
    if web_contrib and web_contrib.get("count"):
        # eindeutige Quell-URLs (max 4) als anklickbare Links unter der Antwort
        urls, seen = [], set()
        for h in web_contrib.get("hits", []):
            u = h.get("source", "")
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        line = f"🌐 Web-Recherche: {web_contrib['count']} Treffer"
        if urls:
            line += "\n" + "\n".join(f"   • {u}" for u in urls[:4])
        footer.append(line)
        meta["web_count"] = web_contrib["count"]
        meta["web_explicit"] = web_contrib.get("explicit", False)
        meta["web_urls"] = urls[:4]
    if facts_used:
        footer.append(f"🔖 {facts_used} verbürgte(r) Fakt(en) als Grounding")
    if retrieval and retrieval.get("count"):
        footer.append(f"📚 General-Vault: {retrieval['count']} Treffer "
                      f"(beste Distance {retrieval.get('best_distance', 0):.1f})")
    if code and code.get("count"):
        footer.append(f"💻 Code-Vault: {code['count']} Treffer "
                      f"(beste Distance {code.get('best_distance', 0):.1f})")
    missing = expected - set(contribs)
    if missing:
        footer.append(f"⚠️ Ausstehend geblieben: {', '.join(sorted(missing))}")
    if footer:
        parts.append("\n".join(footer))

    text = "\n\n".join(p for p in parts if p) or "⚠️ Keine Antwort verfügbar."
    return text, meta


# ── Multi-Model-Ensemble (vibelike-inspiriert) ──────────────────────────────

# Capability-Map: relative Stärke pro Provider (erweiterbar für Claude/Gemini).
# Skala 0–1; höher = vertrauenswürdigeres Modell bekommt mehr Gewicht.
_CAPABILITY = {
    "llm": 0.65,            # lokales Ollama (qwen2.5:7b)
    "llm_deepseek": 0.82,   # DeepSeek-Chat
}

# Frontend-Labels (erweiterbar für Claude/Gemini)
_MODEL_LABELS = {
    "llm": "Ollama (lokal)",
    "llm_deepseek": "DeepSeek-Chat",
}

_WORD_RE = re.compile(r'\b\w{5,}\b')


def _keyword_overlap(answer: str, other_answers: dict[str, str]) -> float:
    """Keyword-Überlappung einer Antwort mit allen anderen (0–1).

    Port von vibelike/consensus.py:_calc_overlap_score:
    Keywords ≥ 5 Zeichen, normalisiert gegen max mögliche Matches.
    """
    if not other_answers or not answer:
        return 0.0
    words = set(w.lower() for w in _WORD_RE.findall(answer))
    if not words:
        return 0.0
    total = 0
    for other in other_answers.values():
        other_words = set(w.lower() for w in _WORD_RE.findall(other))
        total += len(words & other_words)
    max_possible = len(words) * max(len(other_answers), 1)
    return min(total / max_possible, 1.0)


def _detect_gaps(answer: str, other_answers: dict[str, str]) -> list[str]:
    """Themen, die ≥ 2 andere Modelle nennen, aber diese Antwort nicht.

    Port von vibelike/consensus.py:_detect_gaps — Keywords ≥ 6 Zeichen,
    brauchen mindestens 2 andere als Beleg.
    """
    if len(other_answers) < 2:
        return []
    gap_re = re.compile(r'\b\w{6,}\b')
    my_words = set(w.lower() for w in gap_re.findall(answer))

    word_counts: dict[str, int] = {}
    for other in other_answers.values():
        seen = set()
        for w in gap_re.findall(other.lower()):
            if w not in seen:
                seen.add(w)
                word_counts[w] = word_counts.get(w, 0) + 1

    gaps = [w for w, count in word_counts.items()
            if count >= 2 and w not in my_words]
    return gaps[:5]


def _build_llm_answer_entry(key: str, contrib: dict | None,
                           winner: bool, score: float | None) -> dict:
    """Baut einen Eintrag für die llm_answers-Liste (Frontend-Rendering)."""
    c = contrib or {}
    content = (c.get("content") or "").strip()
    skipped = bool(c.get("skipped"))
    model_detail = c.get("model", "")
    error = c.get("error", "")
    return {
        "model": key,
        "label": _MODEL_LABELS.get(key, key),
        "content": content,
        "winner": winner,
        "score": round(score, 3) if score is not None else None,
        "skipped": skipped,
        "empty": not content or skipped,
        "model_detail": model_detail,
        "error": error[:200],
    }


def _ensemble_llm_content(contribs: dict, llm_keys: list,
                          verdict) -> tuple[str | None, dict, dict]:
    """Ensemble-Synthese: Score → Winner → Gaps → gesteigerte Antwort.

    Returns:
        llm_content: bereinigter Text (None = normaler Single-Model-Pfad)
        llm_contrib: der Beitrag-Dict des Winners (für Metriken)
        meta: Ensemble-Metadaten (winner, scores, gaps, llm_answers)
    """
    # Alle Antworten einsammeln (auch skipped/leere — für Frontend-Anzeige)
    answers: dict[str, str] = {}
    contrib_map: dict[str, dict] = {}
    all_contribs: dict[str, dict] = {}
    for key in llm_keys:
        c = contribs.get(key)
        if c is not None:
            all_contribs[key] = c
            if not c.get("skipped") and c.get("content", "").strip():
                answers[key] = c["content"]
                contrib_map[key] = c

    if not answers:
        winner = "none"
        scores: dict[str, float] = {}
        winner_key = "llm"
        winner_contrib = contribs.get("llm", {})
        answer_list = [_build_llm_answer_entry(k, all_contribs.get(k),
                                               k == winner_key, None)
                       for k in llm_keys]
        return None, winner_contrib, {
            "ensemble_size": len(llm_keys), "ensemble_winner": winner,
            "llm_answers": sorted(answer_list, key=lambda x: (0 if x["winner"] else 1, x["model"])),
        }

    if len(answers) == 1:
        key = next(iter(answers))
        answer_list = [_build_llm_answer_entry(k, all_contribs.get(k),
                                               k == key, 1.0)
                       for k in llm_keys]
        return answers[key], contrib_map[key], {
            "ensemble_size": len(llm_keys), "ensemble_winner": key,
            "llm_answers": sorted(answer_list, key=lambda x: (0 if x["winner"] else 1, x["model"])),
        }

    # Scoring: 40 % Overlap + 40 % Capability + 20 % Effort (Länge)
    scores: dict[str, float] = {}
    for model, answer in answers.items():
        others = {m: a for m, a in answers.items() if m != model}
        overlap = _keyword_overlap(answer, others)
        capability = _CAPABILITY.get(model, 0.5)
        effort = min(len(answer) / 800, 1.0)  # Längenbonus bis ~800 Zeichen
        scores[model] = 0.40 * overlap + 0.40 * capability + 0.20 * effort

    winner = max(scores, key=scores.get)
    winner_answer = answers[winner]
    winner_contrib = contrib_map[winner]
    others_for_gaps = {m: a for m, a in answers.items() if m != winner}
    gaps = _detect_gaps(winner_answer, others_for_gaps)

    answer_list = [_build_llm_answer_entry(k, all_contribs.get(k),
                                           k == winner, scores.get(k))
                   for k in llm_keys]
    # Winner zuerst, dann nach Score absteigend
    answer_list.sort(key=lambda x: (0 if x["winner"] else 1, -(x["score"] or 0)))

    meta = {
        "ensemble_winner": winner,
        "ensemble_size": len(llm_keys),
        "ensemble_scores": {k: round(v, 3) for k, v in scores.items()},
        "llm_answers": answer_list,
    }

    if gaps:
        gap_note = ("\n\n💡 *Von anderen Modellen zusätzlich erkannt:*\n"
                    + "\n".join(f"  • {g}" for g in gaps))
        return winner_answer + gap_note, winner_contrib, meta

    return winner_answer, winner_contrib, meta
