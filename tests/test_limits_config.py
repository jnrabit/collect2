"""Rang 4: Limits mit echtem Tuning-Wert in die Config (selektiv, ~10 statt 30).

Plus der Fund dabei: retrieval_max_content_chars (alt: MAX_CONTENT=1200)
kappte die Vault-Quellen auf dem Bus, BEVOR llm_doc_chars (1500) sie sah —
die Tiefe-Erhöhung kam nie ganz an. Default jetzt 1600 + Wächter-Test.
"""

from unittest import mock

from collect.agents import ollama
from collect.config import settings


# ── Der Fund: Bus-Cap darf llm_doc_chars nicht kastrieren ────────────────

def test_bus_content_cap_covers_llm_doc_chars():
    """Wächter: wer llm_doc_chars erhöht, muss retrieval_max_content_chars
    mitziehen — sonst wird die Quelle still auf dem Bus gekürzt."""
    assert settings.retrieval_max_content_chars >= settings.llm_doc_chars


def test_retrieval_agent_truncates_to_settings(mini_store, monkeypatch):
    from collect.agents.retrieval import RetrievalAgent
    from collect.bus import InMemoryBus, Message
    from collect.retrieval.service import VaultSearcher
    from tests.conftest import FakeEmbedder

    monkeypatch.setattr(settings, "retrieval_max_content_chars", 50)
    monkeypatch.setattr(settings, "retrieval_max_hits", 2)
    bus = InMemoryBus(prefix="t.")
    searcher = VaultSearcher.__new__(VaultSearcher)  # store/chaos von Hand
    from collect.retrieval.chaos import ChaosRetrieval
    searcher.store = mini_store
    searcher.chaos = ChaosRetrieval(mini_store, engine=None, field=None,
                                    thompson_seed=7)
    RetrievalAgent(bus, searcher, FakeEmbedder(), kind="retrieval").start()
    bus.publish("retrieval_request", Message(
        type="retrieval_request",
        data={"query": "TLS negotiates keys", "subqueries": ["TLS negotiates keys"]},
        correlation_id="l1"))
    resp = [m for ch, m in bus.published if ch == "retrieval_response"][0]
    assert len(resp.data["hits"]) <= 2
    assert all(len(h["content"]) <= 50 for h in resp.data["hits"])


# ── num_predict aus der Config ───────────────────────────────────────────

class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"response": "ok"}


def test_generate_num_predict_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_num_predict", 512)
    captured = {}

    def fake_post(url, json=None, **kw):
        captured.update(json)
        return FakeResponse()

    with mock.patch.object(ollama.requests, "post", side_effect=fake_post):
        ollama.generate("frage")
    assert captured["options"]["num_predict"] == 512


# ── Decompose-/Rewrite-Limits ────────────────────────────────────────────

def test_decompose_max_subqueries_from_settings(monkeypatch):
    from collect.retrieval.decomposer import QueryDecomposer
    monkeypatch.setattr(settings, "decompose_max_subqueries", 2)
    d = QueryDecomposer.__new__(QueryDecomposer)  # ohne Netz/Session
    out = d._clean(["Aspekt eins", "Aspekt zwei", "Aspekt drei", "Aspekt vier"])
    assert len(out) == 2


def test_rewrite_gate_terms_from_settings(monkeypatch):
    from collect.retrieval.rewriter import is_referential
    # "und was bedeutet das für die kleinen server" — 4 Inhaltswörter
    q = "und was bedeutet das für die kleinen server"
    monkeypatch.setattr(settings, "rewrite_max_content_terms", 6)
    assert is_referential(q)
    monkeypatch.setattr(settings, "rewrite_max_content_terms", 1)
    assert not is_referential(q)  # Gate enger → gilt als eigenständig


# ── Websearch-Limits ─────────────────────────────────────────────────────

def test_chunk_text_size_from_settings(monkeypatch):
    from collect.agents.websearch import _chunk_text
    text = "Satz eins ist hier. " * 50  # ~1000 Zeichen
    monkeypatch.setattr(settings, "web_search_page_chunk_chars", 100)
    small = _chunk_text(text)
    monkeypatch.setattr(settings, "web_search_page_chunk_chars", 10000)
    assert len(small) > len(_chunk_text(text)) == 1


def test_websearch_snippet_limits_from_settings(monkeypatch):
    from collect.agents.websearch import WebSearchAgent
    from collect.bus import InMemoryBus, Message

    class FakeSearcher:
        def search(self, q, max_results=None):
            return [{"url": f"http://x/{i}", "title": f"T{i}",
                     "content": "X" * 500} for i in range(10)]

        def fetch_page(self, url, max_chars=8000):
            return ""  # kein Page-Fetch-Anteil

    monkeypatch.setattr(settings, "web_search_enabled", True)
    monkeypatch.setattr(settings, "web_search_max_snippets", 3)
    monkeypatch.setattr(settings, "web_search_snippet_chars", 100)
    monkeypatch.setattr(settings, "web_search_page_fetches", 0)
    bus = InMemoryBus(prefix="t.")
    agent = WebSearchAgent(bus, searcher=FakeSearcher())
    agent.on_request(Message(type="web_request",
                             data={"query": "test", "explicit": True},
                             correlation_id="w1"))
    resp = [m for ch, m in bus.published if ch == "web_response"][0]
    assert len(resp.data["hits"]) == 3            # max_snippets + 0*top_chunks
    assert all(len(h["content"]) <= 100 for h in resp.data["hits"])


# ── Defaults = altes Verhalten (bis auf den dokumentierten 1600er-Fix) ───

def test_limit_defaults():
    assert settings.retrieval_max_hits == 8
    assert settings.retrieval_max_content_chars == 1600  # war 1200 — bewusst!
    assert settings.decompose_max_subqueries == 3
    assert settings.rewrite_max_content_terms == 6
    assert settings.llm_num_predict == 1024
    assert settings.web_search_max_snippets == 6
    assert settings.web_search_snippet_chars == 1200
    assert settings.web_search_page_fetches == 2
    assert settings.web_search_page_chunk_chars == 1000
    assert settings.web_search_page_top_chunks == 3
