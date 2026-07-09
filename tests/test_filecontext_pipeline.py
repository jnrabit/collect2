"""Teil A Integration: Agent, Orchestrator-Gate, Synthese, Learning-Skip, E2E."""

import numpy as np
import pytest

from collect.agents.filecontext import FileContextAgent
from collect.agents.orchestrator import OrchestratorAgent
from collect.agents.response import synthesize
from collect.bus import InMemoryBus, Message
from collect.config import settings
from collect.retrieval.router import CodeRouter
from tests.conftest import FakeEmbedder, fake_embedding


@pytest.fixture
def allow(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "read_paths", [tmp_path])
    return tmp_path


# ── FileContextAgent ──────────────────────────────────────────────────────

def test_file_agent_publishes_response(allow, tmp_path):
    f = tmp_path / "code.py"; f.write_text("def hello():\n    return 'hi'\n")
    bus = InMemoryBus(prefix="t.")
    FileContextAgent(bus, embedder=FakeEmbedder()).start()
    bus.publish("file_request", Message(
        type="file_request", data={"query": "was macht hello?", "paths": [str(f)]},
        correlation_id="f1"))
    out = [m for ch, m in bus.published if ch == "file_response"]
    assert len(out) == 1
    assert out[0].data["chunk_count"] >= 1
    assert str(f) in out[0].data["paths"]


# ── Orchestrator-Gate ─────────────────────────────────────────────────────

def _orch(bus, **kw):
    emb = FakeEmbedder()
    agent = OrchestratorAgent(bus, CodeRouter(emb.embed_one, centroid=None),
                              rewrite_fn=lambda q, h: {"applied": False, "rewritten": q}, **kw)
    agent.start()
    return bus


def test_orchestrator_adds_file_to_manifest(allow, tmp_path):
    f = tmp_path / "x.py"; f.write_text("x = 1")
    bus = _orch(InMemoryBus(prefix="t."))
    bus.publish("user_query", Message(
        type="user_query", data={"query": f"lies {f}"}, correlation_id="o1"))
    manifest = [m for ch, m in bus.published if ch == "response_manifest"][0]
    assert "file" in manifest.data["expected"]
    freq = [m for ch, m in bus.published if ch == "file_request"]
    assert len(freq) == 1 and str(f) in freq[0].data["paths"]


def test_orchestrator_pathless_query_unchanged(allow):
    bus = _orch(InMemoryBus(prefix="t."))
    bus.publish("user_query", Message(
        type="user_query", data={"query": "was ist TLS?"}, correlation_id="o2"))
    manifest = [m for ch, m in bus.published if ch == "response_manifest"][0]
    assert "file" not in manifest.data["expected"]     # 0-Diff zum Normalfluss
    assert not [m for ch, m in bus.published if ch == "file_request"]


def test_orchestrator_rejects_disallowed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "read_paths", [tmp_path / "erlaubt"])
    (tmp_path / "erlaubt").mkdir()
    outside = tmp_path / "geheim.py"; outside.write_text("secret")
    bus = _orch(InMemoryBus(prefix="t."))
    bus.publish("user_query", Message(
        type="user_query", data={"query": f"lies {outside}"}, correlation_id="o3"))
    # Ablehnung: file-Manifest + file_response(rejected), KEIN retrieval/llm
    manifest = [m for ch, m in bus.published if ch == "response_manifest"][0]
    assert manifest.data["expected"] == ["file"]
    assert not [m for ch, m in bus.published if ch == "retrieval_request"]
    assert not [m for ch, m in bus.published if ch == "llm_request"]
    fresp = [m for ch, m in bus.published if ch == "file_response"][0]
    assert str(outside) in fresp.data["rejected"]


# ── Synthese ──────────────────────────────────────────────────────────────

def _state(contribs, expected):
    return {"query": "q", "expected": set(expected), "contribs": contribs,
            "reply_to": "r", "finalized": False, "started_at": 0}


def test_synthesize_file_grounds_and_footers():
    text, meta = synthesize(_state({
        "file": {"paths": ["/tmp/foo.py"], "chunk_count": 2,
                 "chunks": [{"path": "/tmp/foo.py", "text": "code"}], "rejected": []},
        "llm": {"content": "foo.py definiert eine add-Funktion."},
    }, ["file", "llm"]))
    assert "add-Funktion" in text
    assert "📄 Datei: foo.py" in text
    assert meta["file_chunks"] == 2


def test_synthesize_file_lifts_fallback():
    # Vault FALLBACK, aber Datei geerdet → LLM-Antwort NICHT unterdrückt
    text, _ = synthesize(_state({
        "retrieval": {"zone": "FALLBACK", "best_distance": 80.0, "count": 0, "hits": []},
        "file": {"paths": ["/tmp/x.py"], "chunk_count": 1,
                 "chunks": [{"path": "/tmp/x.py", "text": "c"}], "rejected": []},
        "llm": {"content": "Antwort aus der Datei."},
    }, ["retrieval", "file", "llm"]))
    assert "Antwort aus der Datei." in text
    assert "außerhalb des indizierten Wissensbereichs" not in text


def test_synthesize_renders_rejection():
    text, meta = synthesize(_state({
        "file": {"paths": [], "chunk_count": 0, "chunks": [],
                 "rejected": ["/etc/passwd"]},
    }, ["file"]))
    assert "🚫 Pfad nicht freigegeben" in text and "/etc/passwd" in text
    assert meta["file_rejected"] == ["/etc/passwd"]


# ── LLM: Datei-Block + kein Skip ──────────────────────────────────────────

def test_llm_includes_file_block_and_does_not_skip():
    from collect.agents.llm import LLMAgent
    prompts = []

    def capture(prompt, system="", on_token=None, **kw):
        prompts.append(prompt)
        return "ok", {}

    bus = InMemoryBus(prefix="t.")
    LLMAgent(bus, generate_fn=capture).start()
    bus.publish("llm_request", Message(
        type="llm_request",
        data={"query": "was macht x.py?", "needs": ["retrieval", "file"]},
        correlation_id="l1"))
    # Vault FALLBACK — würde ohne Datei zum Skip führen
    bus.publish("retrieval_response", Message(
        type="retrieval_response",
        data={"zone": "FALLBACK", "best_distance": 90.0, "count": 0, "hits": []},
        correlation_id="l1"))
    bus.publish("file_response", Message(
        type="file_response",
        data={"paths": ["/tmp/x.py"], "chunk_count": 1,
              "chunks": [{"path": "/tmp/x.py", "text": "def x(): return 42"}]},
        correlation_id="l1"))
    assert len(prompts) == 1                      # generiert (nicht übersprungen)
    assert "DATEIINHALT" in prompts[0]
    assert "return 42" in prompts[0]


# ── LearningAgent: Dateikontext nicht ossifizieren ────────────────────────

def test_learning_skips_file_context(tmp_path, monkeypatch):
    from collect.agents.learning import LearningAgent
    monkeypatch.setattr(settings, "triplet_log_file", tmp_path / "t.jsonl")

    class SpyExtractor:
        calls = 0
        def extract_and_stage(self, text, store, source=""):
            SpyExtractor.calls += 1
            return []

    bus = InMemoryBus(prefix="t.")
    LearningAgent(bus, extractor=SpyExtractor()).start()
    bus.publish("answer_recorded", Message(
        type="answer_recorded",
        data={"query": "q", "text": "Antwort über die Datei.", "zone": "TRUST",
              "has_file_context": True},
        correlation_id="a1"))
    assert SpyExtractor.calls == 0                # kein Staging bei Dateikontext
    assert (tmp_path / "t.jsonl").exists()        # Triplet trotzdem geloggt
