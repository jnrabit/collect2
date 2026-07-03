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
    cache_dir: Path = Field(default_factory=lambda: Path.home() / "collect2" / "cache")

    knowledge_vault_file: Optional[Path] = None   # default: data_dir/monolith_archive.monolith
    knowledge_cache_file: Optional[Path] = None   # default: data_dir/monolith_embedding_cache.pkl
    knowledge_field_file: Optional[Path] = None   # default: data_dir/resonance_field.pkl
    code_vault_file: Optional[Path] = None        # default: data_dir/code_archive.monolith
    code_cache_file: Optional[Path] = None        # default: data_dir/code_embedding_cache.pkl
    code_field_file: Optional[Path] = None        # default: data_dir/code_resonance_field.pkl
    code_centroid_file: Optional[Path] = None     # default: data_dir/code_centroid.npy

    # ── Redis ────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    channel_prefix: str = Field(
        default="c2.",
        description="Namespace für alle Bus-Channels — verhindert Kollision mit "
                    "dem Alt-System, das auf demselben Redis läuft",
    )

    # ── Agenten ─────────────────────────────────────────────────────────
    heartbeat_interval: float = 10.0
    response_deadline: float = Field(
        default=120.0,
        description="Deadline (s) für die Manifest-Finalisierung im ResponseAgent",
    )
    plan_deadline: float = Field(
        default=300.0,
        description="Deadline (s) für Plan-Queries (Kaskade braucht länger)",
    )
    llm_timeout: float = 120.0
    decide_timeout: float = 90.0
    step_timeout: float = 60.0
    max_plan_steps: int = 6

    # ── Executor (Plan-Schritte) ────────────────────────────────────────
    executor_workspace: Path = Field(
        default_factory=lambda: Path.home() / "collect2" / "workspace",
        description="Writes/mkdir/rmdir nur unterhalb dieses Verzeichnisses",
    )
    executor_read_roots: list[Path] = Field(
        default_factory=lambda: [Path.home() / "collect2", Path.home() / "collect"],
        description="read/list/search nur unterhalb dieser Wurzeln",
    )
    executor_allow_execute: bool = Field(
        default=False,
        description="execute:-Schritte (Skripte starten) — default aus (Sicherheit)",
    )

    # ── Grounding & Lernen (Ossifikat) ──────────────────────────────────
    ossifikat_db: Path = Field(
        default_factory=lambda: Path.home() / "collect2" / "data" / "ossifikat.db",
        description="Triple-Store; verbürgte Fakten erden Antworten",
    )
    ground_on_facts: bool = True
    fact_top_k: int = 3
    fact_max_distance: float = Field(
        default=0.55,
        description="Cosine-Distanz-Schwelle für Fakt-Relevanz (vibelike-Wert)",
    )
    extract_facts: bool = Field(
        default=True,
        description="TRUST-Antworten per LLM in Staging-Tripel zerlegen "
                    "(menschliche Bestätigung via ossifikat-CLI macht sie verbürgt)",
    )
    triplet_log_file: Optional[Path] = None  # default: log_dir/triplets.jsonl

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
    translate_model: str = Field(
        default="gemma2:2b",
        description="Kleines Modell für DE→EN-Query-Übersetzung (Pre-Retrieval)",
    )
    decompose_model: str = Field(
        default="qwen2.5:3b",
        description="Kleines Modell für Mehr-Aspekt-Query-Zerlegung (Pre-Retrieval)",
    )
    translate_enabled: bool = True
    decompose_enabled: bool = True

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
            "knowledge_field_file": "resonance_field.pkl",
            "code_vault_file": "code_archive.monolith",
            "code_cache_file": "code_embedding_cache.pkl",
            "code_field_file": "code_resonance_field.pkl",
            "code_centroid_file": "code_centroid.npy",
        }
        for attr, filename in defaults.items():
            if getattr(self, attr) is None:
                setattr(self, attr, self.data_dir / filename)
        if self.triplet_log_file is None:
            self.triplet_log_file = self.log_dir / "triplets.jsonl"


settings = CollectSettings()
