"""VaultStore: Laden, Matrix, id→doc-Index."""

from collect.retrieval.store import VaultStore
from tests.conftest import DIM, DOCS


def test_store_loads_archive_and_cache(mini_store):
    assert mini_store.ready
    assert len(mini_store.archive) == len(DOCS)
    assert mini_store.matrix.shape == (len(DOCS), DIM)
    assert set(mini_store.id_map) == {d["id"] for d in DOCS}


def test_doc_index_lookup(mini_store):
    doc = mini_store.get_doc("doc-tls")
    assert doc is not None and doc["title"] == "TLS Handshake"
    assert mini_store.doc_text("doc-tls") == "TLS negotiates keys"
    assert mini_store.get_doc("gibtsnicht") is None
    assert mini_store.doc_text("gibtsnicht") == ""


def test_store_without_files(tmp_path):
    s = VaultStore(tmp_path / "fehlt.monolith", tmp_path / "fehlt.pkl")
    assert not s.ready
    assert s.archive == [] and s.matrix is None
