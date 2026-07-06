"""Harvest: Ingest-Roundtrip, Dedupe, Qualität, Backup/Verify, Wiki-Adapter."""

import pickle

import numpy as np
import pytest

from collect.harvest.ingest import (
    IngestResult,
    VaultIngest,
    content_hash,
    looks_latin,
)
from collect.retrieval.vault import Vault
from tests.conftest import DIM, FakeEmbedder


def _doc(i, content=None):
    return {"id": f"WIKI-en-Doc_{i}", "source": "WIKIPEDIA",
            "sector": "GENERAL_KNOWLEDGE", "title": f"Doc {i}",
            "content": content or ("Inhalt " * 40 + f"nummer {i}"),
            "lang": "en", "timestamp": "2026-07-06"}


@pytest.fixture
def seeded(tmp_path):
    """Vault + Cache mit 2 Bestandsdocs; gibt (ingest, vault_file, cache_file)."""
    vault_file = tmp_path / "vault.monolith"
    cache_file = tmp_path / "cache.pkl"
    archive = [_doc(0), _doc(1)]
    Vault(vault_file).save(archive)
    with open(cache_file, "wb") as f:
        pickle.dump({d["id"]: np.ones(DIM, dtype=np.float32) for d in archive}, f)
    ingest = VaultIngest(embedder=FakeEmbedder(),
                         vault_file=vault_file, cache_file=cache_file)
    return ingest, vault_file, cache_file


# ── Roundtrip ────────────────────────────────────────────────────────────

def test_ingest_adds_and_persists(seeded):
    ingest, vault_file, cache_file = seeded
    res = ingest.ingest([_doc(2), _doc(3)])
    assert res.committed and res.added == 2 and res.total_after == 4

    archive = Vault(vault_file).load()
    assert {d["id"] for d in archive} == {f"WIKI-en-Doc_{i}" for i in range(4)}
    with open(cache_file, "rb") as f:
        cache = pickle.load(f)
    assert set(cache) == {d["id"] for d in archive}          # Keys == IDs
    assert cache["WIKI-en-Doc_2"].shape == (DIM,)
    assert cache["WIKI-en-Doc_2"].dtype == np.float32


def test_backup_created_and_named(seeded):
    ingest, vault_file, _ = seeded
    res = ingest.ingest([_doc(9)])
    assert len(res.backups) == 2
    assert any(".bak-" in b for b in res.backups)
    # Backup enthält den ALTEN Stand (2 Docs)
    bak = next(b for b in res.backups if "vault" in b)
    assert len(Vault(bak).load()) == 2


# ── Dedupe + Qualität ────────────────────────────────────────────────────

def test_dedupe_by_id(seeded):
    ingest, _, _ = seeded
    res = ingest.ingest([_doc(0), _doc(1)])          # beide Bestand
    assert res.added == 0 and res.skipped_dupe == 2


def test_dedupe_by_content_hash(seeded):
    ingest, _, _ = seeded
    same = _doc(99, content=_doc(0)["content"])       # neue ID, alter Inhalt
    res = ingest.ingest([same])
    assert res.added == 0 and res.skipped_dupe == 1


def test_quality_gate_length_and_script(seeded):
    ingest, _, _ = seeded
    short = _doc(5, content="zu kurz")
    cyrillic = _doc(6, content="Привет мир " * 40)
    res = ingest.ingest([short, cyrillic])
    assert res.added == 0 and res.skipped_quality == 2


def test_intra_batch_dedupe(seeded):
    ingest, _, _ = seeded
    res = ingest.ingest([_doc(7), _doc(7)])           # Duplikat im selben Batch
    assert res.added == 1 and res.skipped_dupe == 1


def test_dry_run_writes_nothing(seeded):
    ingest, vault_file, _ = seeded
    res = ingest.ingest([_doc(2)], dry_run=True)
    assert res.added == 1 and res.committed is False
    assert len(Vault(vault_file).load()) == 2         # unverändert


# ── Verify-Fehler bricht ab (kein Move) ──────────────────────────────────

def test_verify_failure_aborts_without_touching_vault(seeded, monkeypatch):
    ingest, vault_file, cache_file = seeded
    orig = Vault(vault_file).load()

    # Verify sabotieren: Reload liefert zu wenige Docs → RuntimeError
    import collect.harvest.ingest as mod
    real_load = mod.Vault.load
    calls = {"n": 0}

    def flaky_load(self):
        calls["n"] += 1
        docs = real_load(self)
        return docs[:-1] if calls["n"] >= 2 else docs  # 2. Load (Verify) kaputt

    monkeypatch.setattr(mod.Vault, "load", flaky_load)
    res = ingest.ingest([_doc(2)])
    assert res.committed is False and "Verify" in res.error
    # Echter Vault unangetastet (nur tmp/bak berührt)
    monkeypatch.setattr(mod.Vault, "load", real_load)
    assert {d["id"] for d in Vault(vault_file).load()} == {d["id"] for d in orig}


# ── Helfer ───────────────────────────────────────────────────────────────

def test_content_hash_normalizes_whitespace():
    assert content_hash("Hallo   Welt\n") == content_hash("hallo welt")
    assert content_hash("a") != content_hash("b")


def test_looks_latin():
    assert looks_latin("The HTTP protocol defines request methods")
    assert not looks_latin("Привет мир как дела сегодня")
    assert not looks_latin("12345 !!! ###")


def test_ingest_result_summary():
    assert "abgebrochen" in IngestResult(error="boom").summary()
    assert "3 neu" in IngestResult(added=3, total_after=10).summary()


# ── Wikipedia-Adapter (gemockter Fetch) ──────────────────────────────────

def test_wikipedia_harvest_with_mocked_api(monkeypatch):
    from collect.harvest.wikipedia import WikipediaSource
    src = WikipediaSource(lang="en")
    monkeypatch.setattr(src, "search_titles", lambda t, l: ["HTTP", "TLS", "TCP"])
    monkeypatch.setattr("collect.harvest.wikipedia.time.sleep", lambda s: None)

    def fake_fetch(title):
        if title == "TLS":
            return None  # Stub/fehlt → übersprungen
        return {"id": f"WIKI-en-{title}", "title": title,
                "content": "x" * 300, "source": "WIKIPEDIA"}

    monkeypatch.setattr(src, "fetch", fake_fetch)
    docs = list(src.harvest("networking", limit=5, expand=False))
    assert [d["id"] for d in docs] == ["WIKI-en-HTTP", "WIKI-en-TCP"]


def test_wikipedia_skips_known_ids(monkeypatch):
    from collect.harvest.wikipedia import WikipediaSource
    src = WikipediaSource(lang="en")
    monkeypatch.setattr(src, "search_titles", lambda t, l: ["HTTP", "TCP"])
    monkeypatch.setattr("collect.harvest.wikipedia.time.sleep", lambda s: None)
    fetched = []

    def fake_fetch(title):
        fetched.append(title)
        return {"id": f"WIKI-en-{title}", "title": title, "content": "x" * 300}

    monkeypatch.setattr(src, "fetch", fake_fetch)
    docs = list(src.harvest("net", limit=5, expand=False, known_ids={"WIKI-en-HTTP"}))
    assert fetched == ["TCP"]                     # HTTP nie gefetcht (bekannt)
    assert [d["id"] for d in docs] == ["WIKI-en-TCP"]
