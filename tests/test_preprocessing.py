"""Translator + Decomposer: Heuristik-Gates, Cache, graceful Degradation.
LLM-Calls werden gemockt — kein Ollama nötig."""

from collect.retrieval.decomposer import QueryDecomposer, looks_multi_aspect
from collect.retrieval.translator import QueryTranslator, looks_german


# ── Heuristiken ──────────────────────────────────────────────────────────

def test_looks_german():
    assert looks_german("Wie funktioniert der TLS Handshake?")
    assert looks_german("Erkläre mir Quantencomputer mit Beispielen")  # Umlaut-frei, Stopwort
    assert not looks_german("How does TLS handshake work?")


def test_looks_multi_aspect():
    assert looks_multi_aspect("Zusammenhang zwischen Biochemie und Informatik erklären bitte")
    assert not looks_multi_aspect("Was ist TLS?")            # zu kurz
    assert not looks_multi_aspect("Explain the TLS handshake protocol in detail please")  # kein Indikator


# ── Translator ───────────────────────────────────────────────────────────

def _translator(tmp_path, response=None):
    t = QueryTranslator(model="test-model", cache_file=tmp_path / "cache.json")
    t._call_ollama = lambda prompt: response
    return t


def test_translator_skips_english(tmp_path):
    t = _translator(tmp_path, response="SHOULD NOT BE CALLED")
    r = t.translate("How does TLS work?")
    assert r["skipped"] is True
    assert r["translated"] == "How does TLS work?"


def test_translator_translates_and_caches(tmp_path):
    t = _translator(tmp_path, response="How does the TLS handshake work?")
    r = t.translate("Wie funktioniert der TLS Handshake?")
    assert r["translated"] == "How does the TLS handshake work?"
    assert r["cache_hit"] is False

    # Zweiter Aufruf: aus dem Cache, auch wenn das LLM weg ist
    t2 = _translator(tmp_path, response=None)
    r2 = t2.translate("Wie funktioniert der TLS Handshake?")
    assert r2["cache_hit"] is True
    assert r2["translated"] == "How does the TLS handshake work?"


def test_translator_llm_down_falls_back_to_original(tmp_path):
    t = _translator(tmp_path, response=None)
    r = t.translate("Wie funktioniert der TLS Handshake?")
    assert r["translated"] == r["original"]


def test_translator_cleans_messy_response(tmp_path):
    t = _translator(tmp_path, response='English: "How does TLS work?"\nExtra line')
    r = t.translate("Wie funktioniert TLS?")
    assert r["translated"] == "How does TLS work?"


# ── Decomposer ───────────────────────────────────────────────────────────

def _decomposer(tmp_path, response=None):
    d = QueryDecomposer(model="test-model", cache_file=tmp_path / "cache.json")
    d._call_ollama = lambda prompt: response
    return d


def test_decomposer_skips_single_topic(tmp_path):
    d = _decomposer(tmp_path, response={"subqueries": ["SHOULD", "NOT", "CALL"]})
    r = d.decompose("Was ist TLS?")
    assert r["skipped"] is True
    assert r["subqueries"] == ["Was ist TLS?"]


def test_decomposer_splits_multi_aspect(tmp_path):
    d = _decomposer(tmp_path, response={
        "subqueries": ["biochemical explanations", "biophysical methods in computing"]})
    r = d.decompose("Zusammenhang zwischen biochemischen und biophysischen Erklärungen in der IT")
    assert r["skipped"] is False
    assert len(r["subqueries"]) == 2


def test_decomposer_llm_down_falls_back(tmp_path):
    d = _decomposer(tmp_path, response=None)
    r = d.decompose("Unterschied zwischen TCP und UDP im Detail erklärt")
    assert r["subqueries"] == [r["original"]]


def test_decomposer_dedupes_and_caps(tmp_path):
    d = _decomposer(tmp_path, response={
        "subqueries": ["a-thema", "A-Thema", "b-thema", "c-thema", "d-thema"]})
    r = d.decompose("Vergleich zwischen vielen verschiedenen Themen bitte ausführlich")
    assert r["subqueries"] == ["a-thema", "b-thema", "c-thema"]  # dedupe + max 3
