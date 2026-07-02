"""Integration gegen die echten Vaults (~/collect/data) — die Messlatte aus
DESIGN.md §6 Phase 2: gleiche Antwortqualität wie das Alt-System auf einem
festen Query-Set.

Läuft nur lokal (Daten + sentence-transformers vorhanden); in CI geskippt.
Bewusst OHNE Translator/Decomposer (kein Ollama nötig) und mit geseedetem
Thompson-Sampler — so ist der deterministische Kern (Warp-Cosine) das, was
gemessen wird.
"""

import importlib.util

import pytest

from collect.config import settings

_HAS_DATA = (settings.knowledge_vault_file.exists()
             and settings.knowledge_cache_file.exists())
_HAS_ST = importlib.util.find_spec("sentence_transformers") is not None

pytestmark = pytest.mark.skipif(
    not (_HAS_DATA and _HAS_ST),
    reason="echte Vaults oder sentence-transformers nicht verfügbar (CI)")


@pytest.fixture(scope="module")
def service():
    from collect.retrieval.embedding import EmbeddingBackend
    from collect.retrieval.router import CodeRouter
    from collect.retrieval.service import RetrievalService, VaultSearcher

    emb = EmbeddingBackend()

    def _seeded(vault, cache, field_file):
        return VaultSearcher(vault, cache, field_file, thompson_seed=42)

    return RetrievalService(
        embedder=emb, translator=None, decomposer=None,
        router=CodeRouter.from_config(emb.embed_one),
        general=_seeded(settings.knowledge_vault_file,
                        settings.knowledge_cache_file,
                        settings.knowledge_field_file),
        code=_seeded(settings.code_vault_file,
                     settings.code_cache_file,
                     settings.code_field_file),
    )


# Festes Query-Set: Themen, die nachweislich im Vault sind (Wikipedia-CS/RFC/ARXIV)
KNOWN_GOOD = [
    "What is Apache Spark?",
    "How does the TLS handshake work?",
    "transformer neural network attention mechanism",
]


@pytest.mark.parametrize("query", KNOWN_GOOD)
def test_known_topics_are_answerable(service, query):
    r = service.retrieve(query, top_k=30, max_hits=5)
    vault = r.general or r.code
    assert vault is not None and len(vault.hits) > 0
    # Kern-Anspruch: bekannte Themen dürfen NICHT im Hard-Fallback landen
    assert vault.verdict.allows_answer, (
        f"{query!r}: zone={vault.verdict.zone}, best={vault.verdict.best_distance:.1f}")


def test_gibberish_lands_in_fallback_or_gray(service):
    r = service.retrieve("xyzzy plugh frobnicate quux blorp", top_k=30)
    vault = r.general or r.code
    # Nonsens darf keine TRUST-Antwort bekommen (Halluzinations-Schutz)
    assert vault.verdict.zone != "TRUST", (
        f"zone={vault.verdict.zone}, best={vault.verdict.best_distance:.1f}")


def test_code_query_routes_to_code(service):
    route, score = service.router.classify(
        "Fix the Python function that raises IndexError in the parser module")
    assert route in ("code", "both"), f"route={route}, cosine={score:.3f}"


def test_result_docs_have_content(service):
    r = service.retrieve("What is Apache Spark?", max_hits=3)
    for h in (r.general or r.code).hits:
        assert h.content, f"Doc {h.doc_id} ohne Content"
