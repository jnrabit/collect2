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
