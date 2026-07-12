"""Token-Streaming: Ollama-Parser, LLMAgent-Interim-Batching, Meta-Statistik."""

import json
from unittest import mock

from collect.agents import ollama
from collect.agents.llm import LLMAgent
from collect.agents.response import synthesize
from collect.bus import InMemoryBus, Message


# ── ollama.generate_streaming ────────────────────────────────────────────

class FakeStreamResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for c in self._chunks:
            yield json.dumps(c).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_generate_streaming_parses_chunks_and_stats():
    chunks = [
        {"response": "Hal"},
        {"response": "lo "},
        {"response": "Welt"},
        {"done": True, "eval_count": 3, "eval_duration": 100_000_000},  # 0.1s
    ]
    seen = []
    with mock.patch.object(ollama.requests, "post",
                           return_value=FakeStreamResponse(chunks)):
        text, stats = ollama.generate_streaming("prompt", on_token=seen.append)
    assert text == "Hallo Welt"
    assert seen == ["Hal", "lo ", "Welt"]
    assert stats["eval_count"] == 3
    assert stats["tok_per_s"] == 30.0


def test_generate_streaming_without_stats():
    chunks = [{"response": "x"}, {"done": True}]
    with mock.patch.object(ollama.requests, "post",
                           return_value=FakeStreamResponse(chunks)):
        text, stats = ollama.generate_streaming("prompt")
    assert text == "x"
    assert stats == {"eval_count": None, "tok_per_s": None}


# ── LLMAgent: Interim-Publishing + Stats in llm_response ─────────────────

def _llm_with_fake_stream(bus, deltas, stats=None):
    def fake_generate(prompt, system="", on_token=None, **kw):
        for d in deltas:
            if on_token:
                on_token(d)
        return "".join(deltas), (stats or {})
    return LLMAgent(bus, generate_fn=fake_generate)


def _drive(bus, agent, cid="s1"):
    agent.start()
    bus.publish("llm_request", Message(
        type="llm_request",
        data={"query": "q", "original_query": "q", "needs": ["retrieval"]},
        correlation_id=cid))
    bus.publish("retrieval_response", Message(
        type="retrieval_response",
        data={"hits": [{"doc_id": "d", "distance": 40.0, "title": "T",
                        "content": "Inhalt"}],
              "zone": "TRUST", "best_distance": 40.0, "count": 1},
        correlation_id=cid))


def test_llm_publishes_interim_and_final_stats():
    bus = InMemoryBus(prefix="test.")
    # >80 Zeichen erzwingen einen Zwischen-Flush vor dem End-Flush
    agent = _llm_with_fake_stream(
        bus, ["a" * 50, "b" * 50, "c" * 10],
        stats={"eval_count": 110, "tok_per_s": 42.5})
    _drive(bus, agent)

    interim = [m for ch, m in bus.published if ch == "llm_interim"]
    assert len(interim) >= 2  # gebatcht, nicht pro Token
    assert "".join(m.data["delta"] for m in interim) == "a" * 50 + "b" * 50 + "c" * 10
    assert interim[-1].data["tokens"] == 3

    final = [m for ch, m in bus.published if ch == "llm_response"]
    assert len(final) == 1
    assert final[0].data["eval_count"] == 110
    assert final[0].data["tok_per_s"] == 42.5


def test_llm_accepts_plain_string_backend():
    """Alte generate_fn-Signatur (str-Rückgabe, kein on_token) läuft weiter."""
    bus = InMemoryBus(prefix="test.")

    def old_style(prompt, system="", **kw):
        return "Antwort ohne Streaming"

    agent = LLMAgent(bus, generate_fn=old_style)
    _drive(bus, agent, cid="s2")
    final = [m for ch, m in bus.published if ch == "llm_response"]
    assert final[0].data["content"] == "Antwort ohne Streaming"
    assert "eval_count" not in final[0].data


# ── Meta-Statistik in der Synthese ───────────────────────────────────────

def test_synthesize_passes_token_stats():
    _, meta = synthesize({
        "query": "q", "expected": {"retrieval", "llm"},
        "contribs": {
            "retrieval": {"zone": "TRUST", "best_distance": 40.0, "count": 1, "hits": []},
            "llm": {"content": "ok", "eval_count": 210, "tok_per_s": 46.9},
        },
        "reply_to": "r", "finalized": False, "started_at": 0,
    })
    assert meta["tokens"] == 210
    assert meta["tok_per_s"] == 46.9


# ── Stop-Token / Anti-Runaway (Fix: ChatML-Marker-Leak) ──────────────────

def test_sanitize_completion_cuts_at_marker():
    assert ollama.sanitize_completion("Antwort.<|im_start|>user\nblah") == "Antwort."
    assert ollama.sanitize_completion("Text<|im_end|>rest") == "Text"
    assert ollama.sanitize_completion("  sauber  ") == "sauber"
    assert ollama.sanitize_completion("kein marker") == "kein marker"


def test_first_marker_index_finds_earliest():
    assert ollama._first_marker_index("a<|im_end|>b<|im_start|>") == 1
    assert ollama._first_marker_index("keiner") == -1


def test_streaming_truncates_at_chatml_marker():
    # Der reale Bug: Modell läuft über die Turn-Grenze, leakt Template-Token
    chunks = [
        {"response": "Die Antwort ist fertig."},
        {"response": "<|im_start|>user"},
        {"response": "\nEntschuldigung, halluzinierte Runde"},
        {"done": True, "eval_count": 5, "eval_duration": 100_000_000},
    ]
    seen = []
    with mock.patch.object(ollama.requests, "post",
                           return_value=FakeStreamResponse(chunks)):
        text, _ = ollama.generate_streaming("p", on_token=seen.append)
    assert text == "Die Antwort ist fertig."
    assert "<|im_start|>" not in "".join(seen)      # Marker nie an den Client
    assert "halluzinierte" not in text              # Fake-Runde abgeschnitten


def test_streaming_preserves_trailing_whitespace_midstream():
    # Regression: sanitize darf mid-stream KEINE Deltas fälschlich kürzen
    chunks = [{"response": "Wort "}, {"response": "zwei"}, {"done": True}]
    seen = []
    with mock.patch.object(ollama.requests, "post",
                           return_value=FakeStreamResponse(chunks)):
        text, _ = ollama.generate_streaming("p", on_token=seen.append)
    assert seen == ["Wort ", "zwei"]
    assert text == "Wort zwei"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.request_json = None

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_generate_sets_stop_and_num_predict():
    captured = {}

    def fake_post(url, json=None, **kw):
        captured.update(json)
        return FakeResponse({"response": "ok"})

    with mock.patch.object(ollama.requests, "post", side_effect=fake_post):
        ollama.generate("frage")
    opts = captured["options"]
    assert opts["stop"] == ollama._STOP_SEQUENCES
    assert opts["num_predict"] == ollama._NUM_PREDICT_CAP


def test_generate_sanitizes_output():
    with mock.patch.object(ollama.requests, "post",
                           return_value=FakeResponse({"response": "Fertig.<|im_end|>müll"})):
        assert ollama.generate("frage") == "Fertig."


# ── num_ctx + Truncation-Guard (Review #4) ────────────────────────────────

def test_generate_sets_num_ctx():
    captured = {}

    def fake_post(url, json=None, **kw):
        captured.update(json)
        return FakeResponse({"response": "ok"})

    with mock.patch.object(ollama.requests, "post", side_effect=fake_post):
        ollama.generate("frage")
    # OHNE num_ctx würde Ollama auf 4096 abschneiden → Tiefe verpufft
    assert captured["options"]["num_ctx"] == ollama.settings.llm_num_ctx


def test_build_prompt_truncation_guard(monkeypatch):
    from collect.agents.llm import LLMAgent
    from collect.bus import InMemoryBus
    monkeypatch.setattr(ollama.settings, "llm_prompt_char_budget", 2000)
    monkeypatch.setattr(ollama.settings, "llm_doc_chars", 100000)  # Guard soll limitieren
    agent = LLMAgent(InMemoryBus(prefix="t."))
    huge = [{"title": "Doc", "content": "X" * 5000, "distance": 10, "doc_id": "d1"}]
    state = {"query": "wichtige FRAGE", "contribs": {
        "retrieval": {"zone": "TRUST", "hits": huge, "best_distance": 10}}}
    prompt = agent._build_prompt(state, facts=None)
    assert len(prompt) < 3000                    # gekürzt aufs Budget
    assert "wichtige FRAGE" in prompt            # Frage bleibt erhalten
    assert "(gekürzt)" in prompt


def test_llm_auto_web_uses_rewritten_query(monkeypatch):
    from collect.config import settings as s
    monkeypatch.setattr(s, "web_search_enabled", True)
    monkeypatch.setattr(s, "web_search_auto", True)
    from collect.agents.llm import LLMAgent
    from collect.bus import InMemoryBus
    bus = InMemoryBus(prefix="t.")
    LLMAgent(bus, generate_fn=lambda p, system="", on_token=None, **k: ("ok", {})).start()
    bus.publish("llm_request", Message(
        type="llm_request",
        data={"query": "und was gibt es dazu?", "needs": ["retrieval"],
              "rewritten_query": "was gibt es zu Quantencomputing aktuell"},
        correlation_id="rw"))
    bus.publish("retrieval_response", Message(
        type="retrieval_response",
        data={"zone": "GRAUZONE", "best_distance": 55.0, "count": 1, "hits": []},
        correlation_id="rw"))
    web_req = [m for ch, m in bus.published if ch == "web_request"][0]
    assert web_req.data["query"] == "was gibt es zu Quantencomputing aktuell"
