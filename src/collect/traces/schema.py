"""Trace-Schema — ein JSONL-Eintrag pro Agenten-Schritt (ein Modellaufruf).

Kanonische Quelle für das native Tool-Schema (TOOL_DEFINITIONS) und die
Trace-Dataclasses. Der Serving-Agent importiert die Tool-Definitionen von
HIER, nicht umgekehrt — das Trace-Modul ist self-contained.

Das LETZTE assistant-Turn ist das Trainings-Target; alles davor ist Kontext.
Der Collector schreibt zunächst OHNE Think im Target (das echte System hat
keins); Think wird in der Kuration (curate.py) synthetisch ergänzt.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# ── Kanonisches Tool-Schema (natives Qwen/OpenAI-Funktionsformat) ─────────
# 1:1 die deterministische Pipeline. Serving (qwythos/tools.py) importiert diese
# Definitionen und hängt die Executor-Funktionen an — Training und Inferenz
# sehen damit dasselbe Schema.
TOOL_DEFINITIONS: list[dict] = [
    {"type": "function", "function": {
        "name": "retrieve",
        "description": "Suche im lokalen Vault nach relevanten Dokumenten. "
                       "Nutze dies für Wissensfragen, Fakten, Konzepte.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Die Suchanfrage (EN bevorzugt)"},
            "route": {"type": "string", "enum": ["general", "code", "both"],
                      "description": "general=Vault, code=Code-Vault, both=beide"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "translate",
        "description": "Übersetze eine deutsche Query ins Englische. "
                       "Nur nötig wenn die Query DEUTSCHE Stichworte enthält.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Die deutsche Query"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "decompose",
        "description": "Zerlege eine mehrteilige/mehr-Aspekt-Frage in mehrere "
                       "unabhängige Einzelfragen für besseres Retrieval.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Die mehrteilige Frage"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "rewrite",
        "description": "Forme eine referenzielle Folgefrage (die sich auf "
                       "vorherige Antworten bezieht) in eine eigenständige Frage "
                       "um. NUR bei Rückbezügen wie 'und warum?' nötig.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Die referenzielle Frage"},
            "history": {"type": "array", "items": {"type": "object", "properties": {
                "q": {"type": "string"}, "a": {"type": "string"}}},
                "description": "Die letzten Q/A-Paare"}},
            "required": ["query", "history"]}}},
    {"type": "function", "function": {
        "name": "classify",
        "description": "Klassifiziere den Query-Typ: plan (Multi-Step-Aufgabe), "
                       "code (Implementierung), oder knowledge (Wissensfrage).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Die zu klassifizierende Query"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "answer",
        "description": "Generiere die finale Antwort aus den gesammelten Quellen. "
                       "Erst aufrufen wenn alle nötigen Tools durch sind.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Die originale Nutzer-Frage"},
            "context": {"type": "string", "description": "Gesammelte Quellen als Text"}},
            "required": ["query", "context"]}}},
    {"type": "function", "function": {
        "name": "done",
        "description": "Beende die Bearbeitung. Keine weiteren Tool-Calls nötig.",
        "parameters": {"type": "object", "properties": {}}}},
]

# step_kind-Vokabular (Auftrag 1). "rewrite"/"answer" sind die heute realen
# deterministischen Schritte; "tool_call"/"no_op" kommen mit dem Serving-Agent.
STEP_KINDS = frozenset({"rewrite", "tool_call", "answer", "no_op"})
OUTCOMES = frozenset({"trust_reached", "user_corrected", "failed", "unknown"})


def compute_schema_hash() -> str:
    normal = json.dumps(TOOL_DEFINITIONS, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(normal.encode()).hexdigest()[:16]


def valid_tool_names() -> set[str]:
    return {t["function"]["name"] for t in TOOL_DEFINITIONS}


@dataclass
class TraceMeta:
    trace_id: str
    step_kind: str
    step_index: int = 0
    workflow_id: str = ""
    timestamp: str = ""
    collect2_version: str = ""
    tool_schema_hash: str = ""
    outcome: str = "unknown"
    curated: bool = False
    synthetic: bool = False
    extra: dict = field(default_factory=dict)  # site-spezifische Zusatzinfos

    def to_dict(self) -> dict:
        d = asdict(self)
        extra = d.pop("extra") or {}
        return {**d, **extra}  # extra flach ins meta einmischen


@dataclass
class TraceEntry:
    messages: list[dict]
    tools: list[dict]
    meta: TraceMeta

    def to_dict(self) -> dict:
        return {"messages": self.messages, "tools": self.tools,
                "meta": self.meta.to_dict()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict) -> "TraceEntry":
        m = dict(d.get("meta", {}))
        known = {f for f in TraceMeta.__dataclass_fields__ if f != "extra"}
        extra = {k: m.pop(k) for k in list(m) if k not in known}
        meta = TraceMeta(**{k: m[k] for k in known if k in m}, extra=extra)
        return TraceEntry(messages=d.get("messages", []),
                          tools=d.get("tools", []), meta=meta)


def make_trace_id(messages: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def validate_entry(d: dict) -> list[str]:
    """Grundcheck (Pflichtfelder, Rollenfolge plausibel, letztes Turn assistant).

    Gibt eine Liste von Fehlermeldungen zurück (leer = valide). Der schwere
    Template-/Token-Check liegt im Validator (validate.py), damit dieser
    Grundcheck ohne transformers läuft.
    """
    errors: list[str] = []
    msgs = d.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return ["messages fehlt oder leer"]
    if "tools" not in d:
        errors.append("tools-Feld fehlt")
    roles = [m.get("role") for m in msgs]
    if roles[0] != "system":
        errors.append("erste Rolle muss system sein")
    if roles[-1] != "assistant":
        errors.append("letzte Rolle (Target) muss assistant sein")
    for i, m in enumerate(msgs[:-1]):
        if m.get("role") == "assistant" and m.get("tool_calls") and roles[i + 1] != "tool":
            errors.append(f"msg {i}: tool_calls ohne folgendes tool-Turn")
    meta = d.get("meta", {})
    for req in ("trace_id", "step_kind", "tool_schema_hash"):
        if not meta.get(req):
            errors.append(f"meta.{req} fehlt")
    if meta.get("step_kind") and meta["step_kind"] not in STEP_KINDS:
        errors.append(f"unbekanntes step_kind: {meta['step_kind']!r}")
    if meta.get("outcome") and meta["outcome"] not in OUTCOMES:
        errors.append(f"unbekanntes outcome: {meta['outcome']!r}")
    return errors
