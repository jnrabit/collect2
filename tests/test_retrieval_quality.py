"""Retrieval-Qualitäts-Fixes pinnen: Plan-Erkennung, Determinismus, Rerank."""

import numpy as np

from collect.agents.orchestrator import is_plan_query
from collect.retrieval.chaos import ChaosRetrieval, ThompsonSampler
from collect.retrieval.service import lexical_rerank, query_terms
from tests.conftest import fake_embedding


# ── Plan-Erkennung (der 'explain'-Bug des Alt-Systems) ───────────────────

def test_plan_nouns_trigger():
    assert is_plan_query("Erstelle einen Plan für die Migration")
    assert is_plan_query("Wie sieht die Roadmap aus?")
    assert is_plan_query("Baue mir einen Ablaufplan")
    assert is_plan_query("plan the workflow")


def test_imperative_with_action_object_triggers():
    assert is_plan_query("Erstelle ein Verzeichnis notizen und eine Datei darin")
    assert is_plan_query("Organisiere die Schritte für den Umzug")


def test_knowledge_questions_do_not_trigger():
    # Die Fehlerklassen der alten Substring-Heuristik:
    assert not is_plan_query("Can you explain the TLS handshake?")   # 'plan' in 'explain'
    assert not is_plan_query("Wie funktioniert Phase-Locking?")      # 'phase'
    assert not is_plan_query("Was ist ein Projektmanagement-Tool?")  # 'projekt'
    assert not is_plan_query("Erkläre mir Schritt für Schritt die Photosynthese")
    assert not is_plan_query("Welche Aufgabe hat das Ribosom?")      # 'aufgabe'
    assert not is_plan_query("kannst du mir etwas über quantenphysik erzählen")


# ── Deterministisches Scoring ────────────────────────────────────────────

def test_thompson_mean_is_deterministic_and_feedback_shifts():
    ts = ThompsonSampler()  # UNGEeedet — Mean braucht keinen Seed
    ids = ["a", "b", "c"]
    m1, m2 = ts.batch_mean(ids), ts.batch_mean(ids)
    assert np.allclose(m1, m2)
    assert np.allclose(m1, 0.5)  # frische Docs → neutral
    ts.update(["a", "b"], relevant={"a"})
    m3 = ts.batch_mean(ids)
    assert m3[0] > 0.5 > m3[1]  # Feedback wirkt weiter
    assert m3[2] == 0.5


def test_deterministic_search_is_reproducible(mini_store):
    # UNGEeedet + Engine None: Reproduzierbarkeit kommt aus dem Modus selbst
    chaos = ChaosRetrieval(mini_store, engine=None, field=None, deterministic=True)
    q = fake_embedding("TLS negotiates keys")
    r1 = chaos.search(q, top_k=4)
    r2 = chaos.search(q, top_k=4)
    assert r1 == r2
    assert r1[0][0] == "doc-tls"


def test_nondeterministic_mode_still_available(mini_store):
    chaos = ChaosRetrieval(mini_store, engine=None, field=None,
                           deterministic=False, thompson_seed=1)
    assert len(chaos.search(fake_embedding("x"), top_k=2)) == 2


# ── Lexikalisches Rerank ─────────────────────────────────────────────────

DOCS_TEXT = {
    "social": "Handshaking promotes deal-making by signaling cooperative intent",
    "tls": "The Transport Layer Security TLS handshake negotiates keys",
}


def test_query_terms_drops_stopwords():
    assert query_terms("How does the TLS handshake work?") == ["tls", "handshake", "work"]


def test_rerank_prefers_exact_term_match():
    merged = [("social", 47.0), ("tls", 49.0)]  # Embedding mag das Soziale
    out = lexical_rerank("How does the TLS handshake work?", merged,
                         lambda d: DOCS_TEXT[d], boost=10.0)
    assert out[0][0] == "tls"
    # Distanzen unverändert — nur die Reihenfolge
    assert dict(out) == dict(merged)


def test_rerank_disabled_with_zero_boost():
    merged = [("social", 47.0), ("tls", 49.0)]
    out = lexical_rerank("TLS handshake", merged, lambda d: DOCS_TEXT[d], boost=0)
    assert out == merged


def test_rerank_no_terms_is_noop():
    merged = [("a", 1.0)]
    assert lexical_rerank("in on is", merged, lambda d: "x") == merged
