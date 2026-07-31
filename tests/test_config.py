"""Config-Verhalten pinnen: Defaults, env-Override, Pfad-Ableitung."""

from pathlib import Path

from collect.config import CollectSettings


def _fresh(**env):
    """Settings-Instanz ohne .env-Datei, mit optionalen env-Overrides."""
    return CollectSettings(_env_file=None, **env)


def test_defaults_local_first():
    s = _fresh()
    assert s.main_model == "qwen3:8b"
    assert s.code_model == "qwen2.5:7b"
    assert s.embedding_device == "cpu"  # VRAM gehört der LLM (DESIGN.md §5.6)
    assert s.embedding_dim == 384


def test_vault_paths_derive_from_data_dir():
    s = _fresh(data_dir=Path("/srv/vaults"))
    assert s.knowledge_vault_file == Path("/srv/vaults/monolith_archive.monolith")
    assert s.knowledge_cache_file == Path("/srv/vaults/monolith_embedding_cache.pkl")
    assert s.code_vault_file == Path("/srv/vaults/code_archive.monolith")
    assert s.code_centroid_file == Path("/srv/vaults/code_centroid.npy")


def test_explicit_vault_path_wins_over_data_dir():
    s = _fresh(data_dir=Path("/srv/vaults"),
               knowledge_vault_file=Path("/anders/wo.monolith"))
    assert s.knowledge_vault_file == Path("/anders/wo.monolith")
    # nicht explizit gesetzte Pfade folgen weiter data_dir
    assert s.code_vault_file == Path("/srv/vaults/code_archive.monolith")


def test_env_prefix_override(monkeypatch):
    monkeypatch.setenv("COLLECT_MAIN_MODEL", "qwen3:8b")
    monkeypatch.setenv("COLLECT_VAULT_TRUST_THRESHOLD", "60.5")
    s = _fresh()
    assert s.main_model == "qwen3:8b"
    assert s.vault_trust_threshold == 60.5


def test_urls():
    s = _fresh()
    assert s.ollama_url == "http://localhost:11434"
    assert s.redis_url == "redis://localhost:6379/0"


def test_three_zone_thresholds_ordered():
    s = _fresh()
    assert s.vault_trust_threshold < s.vault_soft_max_distance
