"""Rang 1: Prompt-Registry — extern überschreibbar, mit Platzhalter-Validierung.

Kernversprechen: eine editierte Prompt-Datei darf NIE eine Query crashen —
invalide Overrides fallen mit Warnung auf den eingebauten Prompt zurück.
"""

import pytest

from collect import prompts
from collect.config import settings


ALL_NAMES = ["llm_system", "session_summary", "rewrite", "translate",
             "decompose", "workflow_planning", "workflow_codegen",
             "workflow_repair"]


# ── Defaults ohne prompts_dir ─────────────────────────────────────────────

def test_all_prompts_have_defaults():
    for name in ALL_NAMES:
        text = prompts.get_prompt(name)
        assert text and text == prompts.embedded(name)


def test_defaults_format_cleanly():
    # Jeder formatierte Default lässt sich mit seinen kwargs ausfüllen
    assert "{turns}" not in prompts.get_prompt("session_summary").format(turns="T")
    assert "TLS" in prompts.get_prompt("translate").format(query="TLS")
    p = prompts.get_prompt("workflow_codegen").format(
        task="t", strategy="s", written="w", path="p", description="d", current="c")
    assert "TASK: t" in p
    p = prompts.get_prompt("workflow_planning").format(
        task="t", briefing="b", max_files=4)
    assert '"subqueries"' not in p and '"strategy"' in p  # {{…}} → literale Braces
    p = prompts.get_prompt("decompose").format(query="q")
    assert '{"subqueries"' in p  # JSON-Beispiel bleibt intakt


# ── Datei-Override ────────────────────────────────────────────────────────

@pytest.fixture
def pdir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "prompts_dir", tmp_path)
    return tmp_path


def test_valid_override_wins(pdir):
    (pdir / "session_summary.txt").write_text(
        "Fasse zusammen (kurz!):\n{turns}", encoding="utf-8")
    assert prompts.get_prompt("session_summary").startswith("Fasse zusammen")


def test_raw_prompt_override_no_placeholder_needed(pdir):
    (pdir / "llm_system.txt").write_text(
        "Antworte immer auf Englisch.", encoding="utf-8")
    assert prompts.get_prompt("llm_system") == "Antworte immer auf Englisch."


def test_missing_required_placeholder_falls_back(pdir):
    (pdir / "rewrite.txt").write_text(
        "Formuliere um: {query}", encoding="utf-8")  # {history} fehlt
    assert prompts.get_prompt("rewrite") == prompts.embedded("rewrite")


def test_unknown_placeholder_falls_back(pdir):
    # {foo} würde beim .format(**kwargs) mit KeyError crashen → ablehnen
    (pdir / "translate.txt").write_text(
        "Translate {query} into {foo}", encoding="utf-8")
    assert prompts.get_prompt("translate") == prompts.embedded("translate")


def test_broken_brace_syntax_falls_back(pdir):
    (pdir / "translate.txt").write_text(
        "Translate {query} with a stray { brace", encoding="utf-8")
    assert prompts.get_prompt("translate") == prompts.embedded("translate")


def test_empty_file_falls_back(pdir):
    (pdir / "translate.txt").write_text("   \n", encoding="utf-8")
    assert prompts.get_prompt("translate") == prompts.embedded("translate")


def test_optional_placeholder_may_be_dropped(pdir):
    # {briefing}/{max_files} sind optional — nur {task} ist Pflicht
    (pdir / "workflow_planning.txt").write_text(
        "Plane: {task}\nAntworte als JSON.", encoding="utf-8")
    assert "Plane:" in prompts.get_prompt("workflow_planning")
    # .format liefert trotzdem ein Ergebnis (überzählige kwargs sind ok)
    out = prompts.get_prompt("workflow_planning").format(
        task="X", briefing="B", max_files=4)
    assert out == "Plane: X\nAntworte als JSON."


def test_missing_file_uses_default(pdir):
    assert prompts.get_prompt("decompose") == prompts.embedded("decompose")


# ── Export-Roundtrip ─────────────────────────────────────────────────────

def test_export_defaults_roundtrip(tmp_path, monkeypatch):
    written = prompts.export_defaults(tmp_path)
    assert len(written) == len(ALL_NAMES)
    monkeypatch.setattr(settings, "prompts_dir", tmp_path)
    # Unveränderte Exporte sind valide und identisch mit den Defaults
    for name in ALL_NAMES:
        assert prompts.get_prompt(name) == prompts.embedded(name)
    # zweiter Export überschreibt nichts
    assert prompts.export_defaults(tmp_path) == []


# ── Integration: Aufrufstellen nutzen die Registry ───────────────────────

def test_rewriter_uses_override(pdir, monkeypatch):
    (pdir / "rewrite.txt").write_text(
        "CUSTOM {history} | {query}", encoding="utf-8")
    from collect.retrieval.rewriter import rewrite
    captured = {}

    def fake_generate(p, **kw):
        captured["prompt"] = p
        return "Was ist TLS im Detail?"

    monkeypatch.setattr(settings, "rewrite_enabled", True)
    rewrite("und was ist damit?", [{"q": "Was ist TLS?", "a": "Protokoll."}],
            generate_fn=fake_generate)
    assert captured["prompt"].startswith("CUSTOM")
