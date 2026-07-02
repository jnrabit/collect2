"""Zentrale Konfiguration — single source of truth für Pfade, Modelle, Redis.

Pydantic-Settings, env-Prefix `COLLECT_` (z.B. COLLECT_MAIN_MODEL=qwen2.5:7b),
optional aus `.env` im Arbeitsverzeichnis.

Usage:
    from collect.config import settings
    settings.main_model
    settings.knowledge_vault_file  # Path
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Daten-Substrat des Alt-Systems: bleibt bis zur Migration (Phase 5) die
# Default-Quelle für Vaults + Embedding-Caches. Nur Fallback — via env
# COLLECT_DATA_DIR überschreibbar.
_LEGACY_DATA_DIR = Path.home() / "collect" / "data"


class CollectSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COLLECT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Pfade ────────────────────────────────────────────────────────────
    data_dir: Path = Field(default=_LEGACY_DATA_DIR)
    log_dir: Path = Field(default_factory=lambda: Path.home() / "collect2" / "logs")

    knowledge_vault_file: Optional[Path] = None   # default: data_dir/monolith_archive.monolith
    knowledge_cache_file: Optional[Path] = None   # default: data_dir/monolith_embedding_cache.pkl
    code_vault_file: Optional[Path] = None        # default: data_dir/code_archive.monolith
    code_cache_file: Optional[Path] = None        # default: data_dir/code_embedding_cache.pkl
    code_centroid_file: Optional[Path] = None     # default: data_dir/code_centroid.npy

    # ── Redis ────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # ── Modelle (lokal-first, Ollama) ───────────────────────────────────
    ollama_host: str = "localhost"
    ollama_port: int = 11434
    main_model: str = Field(
        default="qwen2.5:7b",
        description="Generalist für Q&A-Synthese (passt GPU-only in 8GB VRAM)",
    )
    code_model: str = Field(
        default="qwen2.5:7b",
        description="Code-Modell; default = main_model, damit nur EIN Modell im VRAM liegt",
    )

    # ── Embeddings ──────────────────────────────────────────────────────
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    embedding_device: Literal["cpu", "cuda"] = Field(
        default="cpu",
        description="cpu default: MiniLM ist auf CPU schnell genug (~20ms/Query), "
                    "und die 8GB VRAM gehören vollständig der LLM",
    )

    # ── Routing & Antwortlogik (Werte aus dem stabilisierten Alt-System) ─
    code_route_high: float = 0.40
    code_route_low: float = 0.25
    vault_trust_threshold: float = 55.0
    vault_soft_max_distance: float = 68.0
    code_vault_trust_threshold: float = 62.0

    # ── Timeouts ────────────────────────────────────────────────────────
    query_timeout: float = 180.0
    retrieval_timeout: float = 8.0

    @property
    def ollama_url(self) -> str:
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def model_post_init(self, __context) -> None:
        defaults = {
            "knowledge_vault_file": "monolith_archive.monolith",
            "knowledge_cache_file": "monolith_embedding_cache.pkl",
            "code_vault_file": "code_archive.monolith",
            "code_cache_file": "code_embedding_cache.pkl",
            "code_centroid_file": "code_centroid.npy",
        }
        for attr, filename in defaults.items():
            if getattr(self, attr) is None:
                setattr(self, attr, self.data_dir / filename)


settings = CollectSettings()
