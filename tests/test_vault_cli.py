"""collect-vault: stats/diff/rebuild/dedupe gegen Miniatur-Vaults."""

import pickle
import time

import numpy as np
import pytest

from collect import vault_cli
from collect.config import settings
from collect.retrieval.vault import Vault


NOW = time.time()

DOCS = [
    {"id": "d1", "title": "Apache Spark", "content": "Spark is a cluster engine",
     "source": "wiki", "timestamp": NOW - 3600},          # vor 1h
    {"id": "d2", "title": "TLS", "content": "TLS negotiates keys",
     "source": "rfc", "timestamp": NOW - 30 * 86400},     # vor 30d
    {"id": "d3", "title": "Spark Kopie", "content": "Spark is a cluster engine",
     "source": "arxiv", "timestamp": NOW - 7200},         # exaktes Duplikat von d1
    {"id": "d4", "title": "Spark near", "content": "  SPARK is a Cluster—Engine!! ",
     "source": "wiki", "timestamp": NOW - 7200},          # Near-Dupe von d1
]


@pytest.fixture
def mini_vault(tmp_path, monkeypatch):
    vault_file = tmp_path / "v.monolith"
    cache_file = tmp_path / "c.pkl"
    Vault(vault_file).save(DOCS)
    # d4 hat KEINEN Vektor (Cache-Lücke); dazu ein Waisen-Vektor "ghost"
    cache = {d["id"]: np.ones(8, dtype=np.float32) for d in DOCS[:3]}
    cache["ghost"] = np.ones(8, dtype=np.float32)
    with open(cache_file, "wb") as f:
        pickle.dump(cache, f)
    monkeypatch.setattr(settings, "knowledge_vault_file", vault_file)
    monkeypatch.setattr(settings, "knowledge_cache_file", cache_file)
    return vault_file, cache_file


# ── stats & Lücken ───────────────────────────────────────────────────────

def test_stats_reports_gaps(mini_vault, capsys):
    assert vault_cli.cmd_stats("general") == 0
    out = capsys.readouterr().out
    assert "Dokumente: 4" in out and "Vektoren:  4" in out
    assert "1 Doc(s) OHNE Vektor" in out
    assert "1 Vektor(en) ohne Doc" in out
    assert "wiki" in out  # Quellen-Verteilung


def test_gaps_helper(mini_vault):
    archive, cache = vault_cli._load("general")
    missing, orphans = vault_cli._gaps(archive, cache)
    assert [d["id"] for d in missing] == ["d4"]
    assert orphans == ["ghost"]


# ── diff ─────────────────────────────────────────────────────────────────

def test_diff_since_window(mini_vault, capsys):
    assert vault_cli.cmd_diff("general", "7d") == 0
    out = capsys.readouterr().out
    assert "3 Doc(s)" in out          # d1, d3, d4 (d2 ist 30d alt)
    assert "TLS" not in out.split("ohne Vektor")[0].replace("Cache", "")
    assert "ohne Vektor" in out       # d4-Lücke gemeldet


def test_parse_since():
    assert vault_cli._parse_since("7d") == 7 * 86400
    assert vault_cli._parse_since("48h") == 48 * 3600
    with pytest.raises(SystemExit):
        vault_cli._parse_since("nächste woche")


# ── rebuild ──────────────────────────────────────────────────────────────

class FakeBackend:
    def embed(self, texts, normalize=True):
        assert normalize is False  # MUSS wie der Ingest embedden
        return [np.full(8, float(len(t)), dtype=np.float32) for t in texts]


def test_rebuild_fills_only_missing(mini_vault, capsys, monkeypatch):
    import collect.retrieval.embedding as embedding
    monkeypatch.setattr(embedding, "get_backend", lambda: FakeBackend())
    assert vault_cli.cmd_rebuild("general", rebuild_all=False, yes=False) == 0
    out = capsys.readouterr().out
    assert "1 von 4" in out.replace(",", "")
    _, cache_file = mini_vault
    with open(cache_file, "rb") as f:
        cache = pickle.load(f)
    assert "d4" in cache                          # Lücke gefüllt
    assert cache["d1"].tolist() == [1.0] * 8      # Bestand NICHT angefasst
    # Backup wurde angelegt
    assert list(cache_file.parent.glob("c.pkl.bak-*"))


def test_rebuild_all_needs_confirmation(mini_vault, monkeypatch, capsys):
    import collect.retrieval.embedding as embedding
    monkeypatch.setattr(embedding, "get_backend", lambda: FakeBackend())
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert vault_cli.cmd_rebuild("general", rebuild_all=True, yes=False) == 1
    assert "Abgebrochen" in capsys.readouterr().out


def test_rebuild_nothing_to_do(mini_vault, capsys, monkeypatch):
    import collect.retrieval.embedding as embedding
    monkeypatch.setattr(embedding, "get_backend", lambda: FakeBackend())
    vault_cli.cmd_rebuild("general", rebuild_all=False, yes=False)
    capsys.readouterr()
    assert vault_cli.cmd_rebuild("general", rebuild_all=False, yes=False) == 0
    assert "Nichts zu tun" in capsys.readouterr().out


# ── prune ────────────────────────────────────────────────────────────────

def test_prune_removes_only_orphans(mini_vault, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "yes")
    assert vault_cli.cmd_prune("general", yes=False) == 0
    _, cache_file = mini_vault
    with open(cache_file, "rb") as f:
        cache = pickle.load(f)
    assert "ghost" not in cache
    assert set(cache) == {"d1", "d2", "d3"}          # Bestand unangetastet
    assert list(cache_file.parent.glob("c.pkl.bak-*"))
    capsys.readouterr()
    assert vault_cli.cmd_prune("general", yes=True) == 0   # idempotent
    assert "Keine Waisen" in capsys.readouterr().out


def test_prune_aborts_without_confirmation(mini_vault, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert vault_cli.cmd_prune("general", yes=False) == 1
    _, cache_file = mini_vault
    with open(cache_file, "rb") as f:
        assert "ghost" in pickle.load(f)             # nichts passiert


# ── dedupe ───────────────────────────────────────────────────────────────

def test_dedupe_reports_exact_and_near(mini_vault, capsys):
    assert vault_cli.cmd_dedupe("general", as_json=False) == 0
    out = capsys.readouterr().out
    assert "NICHTS gelöscht" in out
    assert "Exakte Duplikate: 1 Gruppe(n)" in out     # d1 + d3
    assert "1 weitere Gruppe(n)" in out               # d4 als Near-Dupe
    assert "d3" in out and "d4" in out


def test_dedupe_json(mini_vault, capsys):
    import json
    vault_cli.cmd_dedupe("general", as_json=True)
    data = json.loads(capsys.readouterr().out)
    exact_ids = {e["id"] for g in data["exact"] for e in g}
    near_ids = {e["id"] for g in data["near"] for e in g}
    assert exact_ids == {"d1", "d3"}
    assert "d4" in near_ids
