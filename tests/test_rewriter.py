"""Follow-up-Rewrite: Gate-Heuristik, Rewrite-Funktion, Orchestrator-Durchleitung."""

import pytest

from collect.agents.orchestrator import OrchestratorAgent
from collect.bus import InMemoryBus, Message
from collect.config import settings
from collect.retrieval.rewriter import is_referential, rewrite
from collect.retrieval.router import CodeRouter
from tests.conftest import FakeEmbedder

HISTORY = [{"q": "What is Apache Spark?", "a": "Spark ist eine Cluster-Engine."}]


# ── Gate-Heuristik ───────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "und wofür kann man das nutzen?",
    "wofür wird es eingesetzt?",
    "warum ist das so?",
    "aber wie schnell ist es?",
    "what about this?",
    "und bei HTTPS?",
    "gibt es dafür Beispiele?",
])
def test_referential_queries_detected(query):
    assert is_referential(query) is True


@pytest.mark.parametrize("query", [
    "What is Apache Spark?",
    "Wie funktioniert der TLS Handshake im Detail?",
    "Warum ist der Himmel blau?",          # 'warum' allein reicht nicht
    "Python global interpreter lock",
    "Erkläre mir Quantenverschränkung mit einem Beispiel aus der Physik",
    "",
])
def test_standalone_queries_pass_through(query):
    assert is_referential(query) is False


@pytest.mark.parametrize("query", [
    "wie erkennt man den?",
    "wann ist die bindend?",
    "ab welchem Prozentsatz greift der?",
    "wie wird die gelöscht?",
    "wie lange hält der?",
    "woran ist die zerbrochen?",
    "wer durfte da nicht rein?",
    "welche Stoffe kommen da durch?",
])
def test_article_demonstratives_are_referential(query):
    """Regression: bare "der/die/den" ohne folgendes Nomen zeigt zurück.

    Diese Folgefragen gingen vorher ungerewritten ins Retrieval, weil die
    Pronomenliste nur eindeutige Formen kannte."""
    assert is_referential(query) is True


@pytest.mark.parametrize("query", [
    "Wie funktioniert der TLS Handshake im Detail?",
    "Was macht der Goertzel-Algorithmus?",
    "Wie funktioniert die Photosynthese?",
    "Welche Isolationsstufen kennt SQL?",
])
def test_articles_before_nouns_stay_standalone(query):
    """Gegenprobe: mit folgendem Nomen ist es ein Artikel, kein Rückverweis."""
    assert is_referential(query) is False


# ── Rewrite-Funktion ─────────────────────────────────────────────────────

def test_rewrite_applies_with_history():
    r = rewrite("und wofür kann man das nutzen?", HISTORY,
                generate_fn=lambda p, **kw: "Wofür kann man Apache Spark nutzen?")
    assert r["applied"] is True
    assert r["rewritten"] == "Wofür kann man Apache Spark nutzen?"
    assert r["original"] == "und wofür kann man das nutzen?"


def test_rewrite_skips_standalone_and_empty_history():
    called = []

    def spy(p, **kw):
        called.append(p)
        return "x"

    assert rewrite("What is Apache Spark?", HISTORY, spy)["applied"] is False
    assert rewrite("und wofür das?", [], spy)["applied"] is False
    assert called == []  # LLM wurde nie gerufen


def test_rewrite_llm_failure_falls_back():
    def broken(p, **kw):
        raise RuntimeError("ollama weg")
    r = rewrite("und wofür das?", HISTORY, broken)
    assert r["applied"] is False
    assert r["rewritten"] == r["original"]


def test_rewrite_garbage_output_falls_back():
    r = rewrite("und wofür das?", HISTORY, lambda p, **kw: "x" * 500)  # zu lang
    assert r["applied"] is False


def test_rewrite_disabled_via_settings(monkeypatch):
    monkeypatch.setattr(settings, "rewrite_enabled", False)
    r = rewrite("und wofür das?", HISTORY,
                lambda p, **kw: "sollte nicht laufen")
    assert r["applied"] is False


# ── Orchestrator-Durchleitung ────────────────────────────────────────────

def _bus_orchestrator(rewrite_fn):
    bus = InMemoryBus(prefix="test.")
    emb = FakeEmbedder()
    agent = OrchestratorAgent(bus, CodeRouter(emb.embed_one, centroid=None),
                              rewrite_fn=rewrite_fn)
    agent.start()
    return bus


def test_orchestrator_uses_rewrite_and_keeps_original_subquery():
    bus = _bus_orchestrator(
        lambda q, h: {"original": q, "applied": True, "duration_ms": 1.0,
                      "rewritten": "Wofür kann man Apache Spark nutzen?"})
    bus.publish("user_query", Message(
        type="user_query",
        data={"query": "und wofür kann man das nutzen?", "history": HISTORY},
        correlation_id="rw1"))

    manifests = [m for ch, m in bus.published if ch == "response_manifest"]
    assert manifests[0].data["rewritten_query"] == "Wofür kann man Apache Spark nutzen?"

    retrievals = [m for ch, m in bus.published if ch == "retrieval_request"]
    subs = retrievals[0].data["subqueries"]
    assert "Wofür kann man Apache Spark nutzen?" in subs      # Rewrite sucht
    assert "und wofür kann man das nutzen?" in subs           # Original als Fusion

    stages = [m.data["stage"] for ch, m in bus.published if ch == "progress"]
    assert "rewritten" in stages


def test_orchestrator_without_history_never_rewrites():
    called = []

    def spy(q, h):
        called.append(q)
        return {"original": q, "rewritten": q, "applied": False, "duration_ms": 0}

    bus = _bus_orchestrator(spy)
    bus.publish("user_query", Message(
        type="user_query", data={"query": "What is TLS?"}, correlation_id="rw2"))
    assert called == []  # ohne Historie kein Rewrite-Aufruf
    manifests = [m for ch, m in bus.published if ch == "response_manifest"]
    assert manifests[0].data["rewritten_query"] is None
