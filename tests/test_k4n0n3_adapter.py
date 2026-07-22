"""Tests für den K4N0N3-Adapter (kein Modell, kein Netz — reine Logik)."""

import sys

import pytest

pytest.importorskip("torch")

from collect.k4n0n3 import adapter  # noqa: E402


# ── _sanitize: Reasoning-Modelle ─────────────────────────────────────────

def test_sanitize_keeps_answer_after_think():
    """Regression: der Adapter gab den GEDANKENGANG zurueck statt der Antwort.

    Qwythos oeffnet <think> im Prompt, die Generierung enthaelt nur noch das
    schliessende Tag. Wer am ersten </think> abschneidet, behaelt das Denken."""
    raw = "Der Nutzer will X umformulieren. Also...</think>\n\nWie skaliert Rate-Limiting?"
    assert adapter._sanitize(raw) == "Wie skaliert Rate-Limiting?"


def test_sanitize_strips_full_think_block():
    assert adapter._sanitize("<think>kurz</think>\nAntwort") == "Antwort"


def test_sanitize_passes_plain_text():
    assert adapter._sanitize("Ganz normale Antwort.") == "Ganz normale Antwort."


def test_sanitize_cuts_role_tokens():
    assert adapter._sanitize("Antwort<|im_end|>Rest") == "Antwort"


def test_think_tags_are_not_stop_markers():
    """Als Stop-Token wuerden sie die Generierung sofort abwuergen."""
    assert "<think>" not in adapter.QWYTHOS_STOP
    assert "</think>" not in adapter.QWYTHOS_STOP


# ── Modell-Cache ─────────────────────────────────────────────────────────

class _FakeModel:
    def __init__(self, name):
        self.name = name
        self.tokenizer = None

    def generate(self, prompt, **kw):
        return prompt + "ANTWORT"


def test_model_cache_is_per_model(monkeypatch):
    """Regression: ein globales Singleton lieferte fuer ein zweites Modell
    still das erste zurueck (betrifft k4n0n3_rewrite_model)."""
    monkeypatch.setattr(adapter, "_has_gpu", lambda: True)
    monkeypatch.setattr(adapter, "_k4models", {})
    created = []

    def fake_load(hf_name, **kw):
        created.append(hf_name)
        return _FakeModel(hf_name)

    monkeypatch.setitem(adapter._MODEL_MAP, "modell-a", "hf/a")
    monkeypatch.setitem(adapter._MODEL_MAP, "modell-b", "hf/b")

    class _Stub:
        ZeroFlushModel = staticmethod(fake_load)

        @staticmethod
        def auto_vram_budget():
            return 4096

    monkeypatch.setitem(sys.modules, "k4n0n3", _Stub)
    a = adapter._get_k4model("modell-a")
    b = adapter._get_k4model("modell-b")
    assert a.name == "hf/a" and b.name == "hf/b"
    assert created == ["hf/a", "hf/b"]


def test_qwythos_budget_leaves_room_for_embeddings():
    """3,79 GiB residente Embeddings + Layer-Budget muessen in 8 GB passen —
    per Substring, damit auch der gemergte Pfad (…qwythos-9b-rewrite…) trifft."""
    for name in ("empero-ai/Qwythos-9B-Claude-Mythos-5-1M",
                 "/home/jnrabit/models/qwythos-9b-rewrite-v2-merged"):
        budget = adapter._budget_for(name, lambda: 9999)
        assert budget == 2048 and budget + 3790 < 8192


def test_budget_falls_back_to_auto_for_unknown():
    assert adapter._budget_for("qwen2.5:3b", lambda: 3072) == 3072


# ── Prompt-Rendering / Prompt-Echo ───────────────────────────────────────

def test_generate_in_process_strips_echoed_prompt(monkeypatch):
    """ZeroFlushModel.generate() dekodiert die ganze Sequenz inkl. Prompt."""
    monkeypatch.setattr(adapter.settings, "llm_num_predict", 32)
    out = adapter._generate_in_process(_FakeModel("x"), "Frage?", "", 0.0)
    assert out == "ANTWORT"


def test_render_prompt_uses_chat_template():
    class _Tok:
        chat_template = "vorhanden"

        def apply_chat_template(self, msgs, **kw):
            return "|".join(f"{m['role']}:{m['content']}" for m in msgs)

    class _M:
        tokenizer = _Tok()

    got = adapter._render_prompt(_M(), "Frage", "System")
    assert got == "system:System|user:Frage"


def test_render_prompt_falls_back_without_template():
    class _M:
        tokenizer = None

    assert adapter._render_prompt(_M(), "Frage", "System") == "System\n\nFrage"
