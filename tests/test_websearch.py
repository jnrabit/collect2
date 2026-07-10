"""Tests für Web-Suche und WebSearchAgent."""

import pytest

from collect.search.web import WebSearcher, clean_query_for_search, is_web_request
from collect.bus import InMemoryBus, Message


def test_is_web_request_trigger():
    assert is_web_request("recherchiere im web nach Quantencomputing")
    assert is_web_request("kannst du im internet suchen nach Rust?")
    assert is_web_request("mach eine web recherche zu TLS 1.3")
    assert is_web_request("google mal den Fehler TypeError: NoneType")


def test_is_web_request_no_trigger():
    assert not is_web_request("Was ist eine KI?")
    assert not is_web_request("erkläre mir Quantencomputing")
    assert not is_web_request("wie funktioniert TLS?")


def test_clean_query_for_search():
    result = clean_query_for_search("recherchiere im web nach Quantencomputing")
    assert "recherchiere" not in result.lower()
    assert "Quantencomputing" in result

    result = clean_query_for_search("kannst du im internet suchen nach Rust?")
    assert "Rust" in result
    assert "internet" not in result.lower()


def test_clean_query_no_trigger():
    result = clean_query_for_search("Was ist TLS?")
    assert "TLS" in result


def test_web_searcher_no_results_on_empty():
    searcher = WebSearcher(base_url="http://localhost:1")
    results = searcher.search("")
    assert results == []


def test_web_searcher_connection_refused():
    searcher = WebSearcher(base_url="http://localhost:1", timeout=1.0)
    results = searcher.search("test query")
    assert results == []


def test_web_agent_disabled():
    from collect.agents.websearch import WebSearchAgent
    bus = InMemoryBus()
    agent = WebSearchAgent(bus)
    agent.enabled = False
    msg = Message(type="web_request",
                  data={"query": "test", "explicit": True})
    agent.on_request(msg)
    published = [(ch, m) for ch, m in bus.published
                 if m.type == "web_response"]
    assert len(published) == 1
    assert published[0][1].data.get("skipped") is True


def test_web_agent_publishes_response_on_empty():
    from collect.agents.websearch import WebSearchAgent
    bus = InMemoryBus()
    agent = WebSearchAgent(bus, searcher=WebSearcher(base_url="http://localhost:1", timeout=0.5))
    msg = Message(type="web_request",
                  data={"query": "test", "explicit": True},
                  correlation_id="cid1")
    agent.on_request(msg)
    published = [(ch, m) for ch, m in bus.published
                 if m.type == "web_response"]
    assert len(published) == 1
    assert published[0][1].data.get("count", 0) == 0


# ── Auto-Web im LLMAgent (GRAUZONE/FALLBACK → Web, richtige cid, synthetisiert) ──

def _llm_bus(monkeypatch, auto=True):
    from collect.config import settings
    monkeypatch.setattr(settings, "web_search_enabled", True)
    monkeypatch.setattr(settings, "web_search_auto", auto)
    from collect.agents.llm import LLMAgent
    from collect.bus import InMemoryBus
    bus = InMemoryBus(prefix="t.")
    prompts = []

    def capture(prompt, system="", on_token=None, **kw):
        prompts.append(prompt)
        return "Antwort", {}

    LLMAgent(bus, generate_fn=capture).start()
    return bus, prompts


def _retrieval(bus, zone, cid="w1"):
    bus.publish("retrieval_response", Message(
        type="retrieval_response",
        data={"zone": zone, "best_distance": 55.0, "count": 3, "hits": []},
        correlation_id=cid))


def test_llm_auto_web_on_grauzone_correct_cid(monkeypatch):
    bus, prompts = _llm_bus(monkeypatch)
    bus.publish("llm_request", Message(
        type="llm_request", data={"query": "filme kino augsburg", "needs": ["retrieval"]},
        correlation_id="w1"))
    _retrieval(bus, "GRAUZONE")
    web_reqs = [m for ch, m in bus.published if ch == "web_request"]
    assert len(web_reqs) == 1
    assert web_reqs[0].correlation_id == "w1"    # NICHT cid+"_web" (der alte Bug)
    assert prompts == []                          # wartet auf Web, generiert noch nicht

    # web_response → jetzt generieren MIT Web im Prompt
    bus.publish("web_response", Message(
        type="web_response",
        data={"count": 2, "explicit": False,
              "hits": [{"title": "Kino XY", "content": "Filme heute Abend"}]},
        correlation_id="w1"))
    assert len(prompts) == 1
    assert "WEB-RECHERCHE" in prompts[0] and "Kino XY" in prompts[0]


def test_llm_no_auto_web_on_trust(monkeypatch):
    bus, prompts = _llm_bus(monkeypatch)
    bus.publish("llm_request", Message(
        type="llm_request", data={"query": "q", "needs": ["retrieval"]}, correlation_id="w1"))
    _retrieval(bus, "TRUST")
    assert [m for ch, m in bus.published if ch == "web_request"] == []
    assert len(prompts) == 1                      # direkt generiert, kein Web


def test_llm_auto_web_off_by_default(monkeypatch):
    bus, prompts = _llm_bus(monkeypatch, auto=False)
    bus.publish("llm_request", Message(
        type="llm_request", data={"query": "q", "needs": ["retrieval"]}, correlation_id="w1"))
    _retrieval(bus, "FALLBACK")
    assert [m for ch, m in bus.published if ch == "web_request"] == []
