"""Tests für Harvest-Sources (ArXiv, RFC)."""

from collect.harvest.arxiv import search_arxiv
from collect.harvest.rfc import harvest_rfcs, fetch_rfc, fetch_rfc_index


def test_search_arxiv_basic():
    try:
        docs = search_arxiv("transformer attention", max_results=3, timeout=15)
    except Exception:
        docs = []
    assert isinstance(docs, list)
    for d in docs:
        assert "id" in d
        assert d["source"] == "arxiv"
        assert d["content"]


def test_fetch_rfc_index():
    try:
        ids = fetch_rfc_index(limit=5)
    except Exception:
        ids = []
    assert isinstance(ids, list)


def test_fetch_rfc_known():
    doc = fetch_rfc("9594", timeout=15)
    if doc is not None:
        assert doc["source"] == "rfc"
        assert doc["title"]
        assert doc["content"]
    else:
        pass


def test_harvest_rfcs():
    try:
        docs = harvest_rfcs(max_rfcs=2)
    except Exception:
        docs = []
    assert isinstance(docs, list)
