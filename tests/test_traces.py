"""Tests für die Trace-Pipeline (schema, collector, validator, curate)."""

import json
import os
import stat
from pathlib import Path

import pytest

from collect.traces.schema import (
    TOOL_DEFINITIONS, TraceEntry, TraceMeta, compute_schema_hash,
    make_trace_id, validate_entry,
)
from collect.traces.collector import TraceCollector
from collect.traces.validate import TraceValidator, detect_language, approx_tokens
from collect.traces import curate


def _entry_dict(step_kind="answer", tool_calls=None, target="Eine deutsche Antwort."):
    assistant = {"role": "assistant", "content": target}
    msgs = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Frage auf Deutsch"},
    ]
    if tool_calls:
        msgs.append({"role": "assistant", "tool_calls": tool_calls})
        msgs.append({"role": "tool", "content": "{}"})
    msgs.append(assistant)
    return {
        "messages": msgs, "tools": [],
        "meta": {"trace_id": make_trace_id(msgs), "step_kind": step_kind,
                 "tool_schema_hash": compute_schema_hash()},
    }


# ── Schema ───────────────────────────────────────────────────────────────

def test_schema_hash_deterministic():
    assert compute_schema_hash() == compute_schema_hash()
    assert len(compute_schema_hash()) == 16


def test_validate_entry_ok():
    assert validate_entry(_entry_dict()) == []


def test_validate_entry_missing_tools():
    e = _entry_dict()
    del e["tools"]
    assert any("tools" in m for m in validate_entry(e))


def test_validate_entry_last_must_be_assistant():
    e = _entry_dict()
    e["messages"].append({"role": "user", "content": "noch was"})
    assert any("assistant" in m for m in validate_entry(e))


def test_validate_entry_unknown_step_kind():
    e = _entry_dict(step_kind="frobnicate")
    assert any("step_kind" in m for m in validate_entry(e))


def test_trace_entry_roundtrip():
    e = TraceEntry.from_dict(_entry_dict())
    d = e.to_dict()
    assert d["meta"]["step_kind"] == "answer"
    assert TraceEntry.from_dict(d).to_dict() == d


def test_meta_extra_flattened():
    m = TraceMeta(trace_id="x", step_kind="answer", extra={"zone": "TRUST"})
    assert m.to_dict()["zone"] == "TRUST"


# ── Collector ────────────────────────────────────────────────────────────

def test_collector_record_rotating(tmp_path):
    c = TraceCollector(base_dir=tmp_path)
    tid = c.record("rewrite", _entry_dict("rewrite")["messages"])
    assert tid
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1  # eine Tagesdatei
    loaded = c.load_all()
    assert len(loaded) == 1 and loaded[0]["meta"]["step_kind"] == "rewrite"
    assert loaded[0]["tools"] == []  # tools-Feld immer vorhanden


def test_load_all_ignores_subdir_derivatives(tmp_path):
    """Derivate (curated/) dürfen von load_all NICHT als Traces gelesen werden."""
    c = TraceCollector(base_dir=tmp_path)
    c.record("rewrite", _entry_dict("rewrite")["messages"])
    curated = tmp_path / "curated"
    curated.mkdir()
    (curated / "training_set.jsonl").write_text(
        json.dumps(_entry_dict("rewrite")) + "\n", encoding="utf-8")
    assert len(c.load_all()) == 1  # nur der echte Trace, nicht das Derivat


def test_collector_outcome_separate_file(tmp_path):
    c = TraceCollector(base_dir=tmp_path)
    c.record_outcome("wf-1", "trust_reached")
    assert (tmp_path / "outcomes.jsonl").exists()
    # Outcome-Datei wird von load_all ignoriert (kein Trace)
    assert c.load_all() == []


def test_collector_never_raises_on_readonly(tmp_path):
    """Akzeptanz: Collector-Ausfall bricht keinen Agenten-Pfad."""
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)  # read-only
    c = TraceCollector(base_dir=ro / "sub")  # nicht anlegbar
    try:
        tid = c.record("answer", _entry_dict()["messages"])  # darf NICHT werfen
        assert tid is None  # Fehler wurde geschluckt
    finally:
        os.chmod(ro, stat.S_IRWXU)


def test_record_if_enabled_respects_flag(tmp_path, monkeypatch):
    import collect.traces.collector as col
    monkeypatch.setattr(col.settings, "traces_enabled", False)
    monkeypatch.setattr(col.settings, "traces_dir", tmp_path)
    col._collector = None
    col.record_if_enabled("answer", _entry_dict()["messages"])
    assert not list(tmp_path.glob("*.jsonl"))  # nichts geschrieben


# ── Validator ────────────────────────────────────────────────────────────

def test_detect_language():
    assert detect_language("der die das und ist eine deutsche Antwort hier") == "de"
    assert detect_language("the quick brown fox jumps over lazy") == "other"


def test_validator_flags_sie_register(tmp_path):
    e = _entry_dict(target="Bitte beachten Sie Ihre Einstellungen sorgfältig genau.")
    v = TraceValidator(traces_dir=tmp_path)
    issues = v._check_register_language(e["messages"])
    assert any(i["kind"] == "register" for i in issues)


def test_validator_think_too_long(tmp_path):
    long_think = "wort " * 120
    e = _entry_dict(target=f"<think>{long_think}</think>\nAntwort")
    v = TraceValidator(traces_dir=tmp_path)
    issues = v._check_think(e["messages"])
    assert any(i["kind"] == "think_length" for i in issues)


def test_validator_unknown_tool(tmp_path):
    e = _entry_dict(tool_calls=[{"function": {"name": "erfunden", "arguments": {}}}])
    v = TraceValidator(traces_dir=tmp_path)
    issues = v._check_tool_calls(e["messages"], e)
    assert any(i["kind"] == "tool_call" for i in issues)


def test_validator_histogram_empty():
    assert "note" in TraceValidator._histogram([])


def test_validator_histogram_percentiles():
    h = TraceValidator._histogram(list(range(1, 101)))
    assert h["p50"] <= h["p90"] <= h["p99"] <= h["max"]
    assert h["over_1024"] == 0


# ── Curate ───────────────────────────────────────────────────────────────

def test_synth_think_by_kind():
    assert "eigenständige" in curate.synth_think("rewrite")
    assert "Antwort" in curate.synth_think("answer")


def test_apply_think_inserts_block():
    e = _entry_dict()
    out = curate.apply_think(e, "Kurzer Grund.")
    assert "<think>Kurzer Grund.</think>" in out["messages"][-1]["content"]


def test_negative_unchanged_from_rewrite():
    e = _entry_dict("rewrite", target="Wie robust ist der Goertzel-Algorithmus?")
    e["messages"][1]["content"] = (
        "Formuliere die FOLGEFRAGE um.\n\nGESPRÄCH:\n"
        "Nutzer: Was macht Goertzel?\nAssistent: Er misst eine Frequenz.\n\n"
        "FOLGEFRAGE: und wie robust ist das?\n\nEigenständige Frage:")
    neg = curate.make_negative_unchanged(e)
    assert neg is not None
    # Target ist UNCHANGED, und die eigenständige Frage steht als FOLGEFRAGE drin
    assert neg["messages"][-1]["content"] == "UNCHANGED"
    assert "Wie robust ist der Goertzel-Algorithmus?" in neg["messages"][1]["content"]
    assert "kein vorheriges Gespräch" in neg["messages"][1]["content"]
    # der referenzielle Original-Wortlaut ist NICHT mehr die Folgefrage
    assert "und wie robust ist das?" not in neg["messages"][1]["content"]
    assert neg["meta"]["synthetic"] is True


def test_negative_unchanged_skips_non_rewrite():
    assert curate.make_negative_unchanged(_entry_dict("answer")) is None


def test_negative_unchanged_skips_trivial_target():
    # Zu kurzes/UNCHANGED-Target → kein brauchbares eigenständiges Beispiel
    assert curate.make_negative_unchanged(_entry_dict("rewrite", target="UNCHANGED")) is None
    assert curate.make_negative_unchanged(_entry_dict("rewrite", target="und dafür?")) is None


def test_curate_interactive_writes_marker(tmp_path):
    traces = [_entry_dict("rewrite")]
    path = tmp_path / "curation.jsonl"
    verdicts = iter(["g"])
    result = curate.curate_interactive(
        traces, path, input_fn=lambda _: next(verdicts, "q"), print_fn=lambda *a: None)
    assert result["kept"] == 1
    line = json.loads(path.read_text().splitlines()[0])
    assert line["curated"] is True and line["think"]
