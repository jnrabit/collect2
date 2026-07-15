"""Rang 3: Pattern-Registry — Routing-Wortlisten per JSON erweiterbar.

Kernversprechen wie bei den Prompts: eine editierte JSON darf NIE das
Routing crashen oder ein Alles-matcht-Pattern einschleusen — invalide
Overrides fallen mit Warnung auf die eingebauten Listen zurück.
"""

import json

import pytest

from collect import patterns
from collect.agents.orchestrator import is_code_task, is_plan_query
from collect.config import settings
from collect.retrieval.profiles import auto_detect


ALL_NAMES = ["plan_detection", "code_detection", "profile_signals"]


# ── Defaults ohne patterns_dir ────────────────────────────────────────────

def test_defaults_without_dir():
    for name in ALL_NAMES:
        assert patterns.get_patterns(name) == patterns.embedded(name)


def test_default_behavior_unchanged():
    # Regressionsschutz: identisches Routing wie vor der Externalisierung
    assert is_code_task("Implementiere eine Funktion zur Sortierung")
    assert not is_code_task("Wie funktioniert eine Klasse?")
    assert is_plan_query("Erstelle einen Plan mit allen Schritten")
    assert not is_plan_query("Erkläre mir das bitte")
    assert auto_detect("warum ist der himmel blau") == "resonant"
    assert auto_detect("TLS Handshake") == "balanced"


# ── Datei-Override ────────────────────────────────────────────────────────

@pytest.fixture
def pdir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "patterns_dir", tmp_path)
    return tmp_path


def _write(pdir, name, data):
    (pdir / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_extend_plan_nouns(pdir):
    # Der Use-Case aus der Analyse: Wörter aufnehmen ohne Python-Edit
    data = patterns.embedded("plan_detection")
    data = {**data, "nouns": data["nouns"] + ["sprintziele?"]}
    _write(pdir, "plan_detection", data)
    assert is_plan_query("was sind unsere sprintziele")
    assert is_plan_query("Erstelle einen Plan mit allen Schritten")  # alt bleibt


def test_extend_code_verbs(pdir):
    data = patterns.embedded("code_detection")
    data = {**data, "verbs": data["verbs"] + ["porte?"]}
    _write(pdir, "code_detection", data)
    assert is_code_task("porte das Modul nach Rust")


def test_extend_profile_signals(pdir):
    signals = [{"profile": "chaos", "stems": ["experimentier"], "phrases": []}]
    _write(pdir, "profile_signals", signals)
    assert auto_detect("experimentiere mal mit dem vault") == "chaos"
    # komplette Ersetzung: alte precise-Signale sind weg
    assert auto_detect("was ist TLS") == "balanced"


# ── Invalide Overrides → Fallback ────────────────────────────────────────

def test_broken_json_falls_back(pdir):
    (pdir / "plan_detection.json").write_text("{kein json", encoding="utf-8")
    assert is_plan_query("Erstelle einen Plan mit allen Schritten")


def test_missing_key_falls_back(pdir):
    _write(pdir, "code_detection", {"verbs": ["fixe?"]})  # objects fehlt
    assert patterns.get_patterns("code_detection") == patterns.embedded("code_detection")


def test_empty_string_fragment_falls_back(pdir):
    # "" im Join würde \b(|…)\b zum Alles-Matcher machen — hart ablehnen
    data = patterns.embedded("code_detection")
    _write(pdir, "code_detection", {**data, "verbs": data["verbs"] + [""]})
    assert patterns.get_patterns("code_detection") == patterns.embedded("code_detection")
    assert not is_code_task("Wie funktioniert eine Klasse?")


def test_invalid_regex_fragment_falls_back(pdir):
    data = patterns.embedded("plan_detection")
    _write(pdir, "plan_detection", {**data, "nouns": data["nouns"] + ["(kaputt"]})
    assert patterns.get_patterns("plan_detection") == patterns.embedded("plan_detection")


def test_unknown_profile_falls_back(pdir):
    _write(pdir, "profile_signals",
           [{"profile": "geist", "stems": ["x"], "phrases": []}])
    assert patterns.get_patterns("profile_signals") == patterns.embedded("profile_signals")


def test_rule_without_signals_falls_back(pdir):
    _write(pdir, "profile_signals",
           [{"profile": "chaos", "stems": [], "phrases": []}])
    assert patterns.get_patterns("profile_signals") == patterns.embedded("profile_signals")


# ── Export-Roundtrip ─────────────────────────────────────────────────────

def test_export_defaults_roundtrip(tmp_path, monkeypatch):
    written = patterns.export_defaults(tmp_path)
    assert len(written) == len(ALL_NAMES)
    monkeypatch.setattr(settings, "patterns_dir", tmp_path)
    for name in ALL_NAMES:
        assert patterns.get_patterns(name) == patterns.embedded(name)
    assert patterns.export_defaults(tmp_path) == []  # überschreibt nie
