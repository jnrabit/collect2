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
    # Anzeigename (Chat-Header, REPL, API-Titel). Technischer Name bleibt
    # `collect` — wie beim Vorbild: vibelike hieß zur Anzeige hótr̥, der Code
    # blieb vibelike ("Rebrand ist rein kosmetisch", vibelike/README).
    display_name: str = "collect2 · hótr̥"
    model_config = SettingsConfigDict(
        env_prefix="COLLECT_",
        # Repo-.env zuerst, cwd-.env überschreibt — so funktionieren die CLIs
        # (collect-ask/-repl/-api) aus jedem Arbeitsverzeichnis heraus.
        env_file=(str(Path.home() / "collect2" / ".env"), ".env"),
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

    # ── Ad-hoc-Dateikontext + lokaler Ingest ────────────────────────────
    file_context_enabled: bool = True
    read_paths: Optional[list[Path]] = Field(
        default=None,
        description="Allowlist für Ad-hoc-Datei-Lesen; None = executor_read_roots. "
                    "Erweiterung ist bewusster Opt-in (COLLECT_READ_PATHS, CSV).",
    )
    file_direct_threshold: int = Field(
        default=8192, description="Datei < N Bytes → ganz in den Prompt (kein Chunking)")
    file_max_bytes: int = Field(
        default=2_000_000, description="max. Gesamtbytes über alle gelesenen Dateien")
    file_max_files: int = Field(
        default=25, description="max. Dateien bei Verzeichnis-Lesen")
    file_max_depth: int = Field(default=3, description="max. Verzeichnistiefe")
    file_chunk_chars: int = Field(default=1200, description="Chunk-Größe (Zeichen)")
    file_top_chunks: int = Field(default=6, description="relevanteste Chunks in die Synthese")
    file_extensions: str = Field(
        default=".py,.js,.ts,.go,.rs,.java,.c,.h,.cpp,.sh,.md,.txt,.rst,"
                ".toml,.yaml,.yml,.json,.cfg,.ini",
        description="Endungs-Allowlist (CSV) fürs Verzeichnis-/Local-Lesen")

    # ── Code-Workflow (Phase 6) ─────────────────────────────────────────
    workflow_repo: Optional[Path] = Field(
        default=None,
        description="Ziel-Repo für Code-Workflows; default: executor_workspace/repo. "
                    "Verify führt dort pytest (= generierten Code!) aus — bewusst "
                    "auf dieses Verzeichnis begrenzt.",
    )
    workflow_verify_timeout: float = 120.0
    workflow_max_files: int = 4
    workflow_repair_rounds: int = Field(
        default=2,
        description="Bei rotem Verify: Fehler-Output zurück ans LLM, Datei "
                    "korrigieren, erneut verifizieren (0 = aus). Real brauchte "
                    "Runde 1 den vergessenen Import, Runde 2 die überstrenge "
                    "Test-Erwartung.",
    )
    workflow_idioms_enabled: bool = Field(
        default=True,
        description="Idiom-System: Few-Shot-Patterns pro Task-Typ in die "
                    "Code-Generierung injizieren",
    )

    # ── Sessions ─────────────────────────────────────────────────────────
    session_enabled: bool = True
    session_db: Path = Field(
        default_factory=lambda: Path.home() / "collect2" / "data" / "sessions.db",
        description="SQLite-DB für persistente Gesprächs-Sessions",
    )
    session_max_turns: int = Field(
        default=5, description="Max Turns pro Session vor Auto-Summarization")
    session_summary_model: str = Field(
        default="",
        description="Modell für Session-Summarization; leer = decompose_model")

    # ── Web-Recherche ────────────────────────────────────────────────────
    web_search_enabled: bool = Field(
        default=True,
        description="Web-Recherche als FALLBACK + expliziter Trigger "
                    "('recherchiere im web' etc.)",
    )
    web_search_auto: bool = Field(
        default=False,
        description="Automatische Web-Recherche bei Vault-FALLBACK (Opt-in). "
                    "Expliziter Trigger ('recherchiere im web') funktioniert "
                    "unabhängig hiervon.",
    )
    web_search_url: str = Field(
        default="http://localhost:8888",
        description="SearXNG-Instanz (selbst-gehostet, keine Cloud-Abhängigkeit)",
    )
    web_search_timeout: float = 15.0
    web_search_results: int = Field(
        default=5, description="max Snippets in die Synthese")
    web_search_trigger: str = Field(
        default="recherchiere|recherchieren|such im web|suche im web|google|"
                "web suche|web recherche|im internet|online suche",
        description="Regex-Trigger für explizite Web-Recherche-Anfrage (case-insensitiv)")

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

    # ── REST-API ────────────────────────────────────────────────────────
    api_host: str = Field(default="127.0.0.1",
                          description="Bind-Adresse; 127.0.0.1 = nur localhost. "
                                      "Nicht-localhost-Bind erfordert api_token "
                                      "(Preflight verweigert sonst den Start).")
    api_port: int = 8767  # 8766 belegt das Alt-System
    api_token: str = Field(
        default="",
        description="Bearer-Token/API-Key. Leer = offen (nur mit localhost-Bind "
                    "erlaubt). Gesetzt = Auth auf teuren/sensiblen Endpoints.",
    )
    api_cors_origins: str = Field(
        default="",
        description="Komma-separierte CORS-Allowlist. Leer = nur die eigene "
                    "localhost-Origin (http://127.0.0.1:PORT + http://localhost:PORT).",
    )
    api_rate_expensive: int = Field(
        default=20, description="teure Endpoints (query/ws): Aufrufe pro Minute/Key")
    api_rate_light: int = Field(
        default=120, description="leichte Endpoints (facts): Aufrufe pro Minute/Key")
    api_max_body_bytes: int = Field(
        default=65536, description="Body-Cap gegen Riesen-Payloads (413 darüber)")

    @property
    def api_default_origins(self) -> list[str]:
        return [f"http://127.0.0.1:{self.api_port}",
                f"http://localhost:{self.api_port}"]

    @property
    def api_is_localhost(self) -> bool:
        return self.api_host in ("127.0.0.1", "::1", "localhost")

    @property
    def effective_read_paths(self) -> list[Path]:
        """Datei-Lese-Allowlist: read_paths, sonst die Executor-Grenzen."""
        return self.read_paths if self.read_paths else self.executor_read_roots

    @property
    def file_ext_set(self) -> set[str]:
        return {e.strip().lower() for e in self.file_extensions.split(",") if e.strip()}

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
        default="",
        description="DE→EN-Query-Übersetzung (Pre-Retrieval); leer = main_model. "
                    "Kleine Modelle (gemma2:2b/qwen2.5:3b) übersetzen Fachbegriffe "
                    "falsch ('Quantenverschränkung'→'Quantum confinement') und "
                    "korrumpieren das Retrieval unbemerkt — daher der Generalist.",
    )
    decompose_model: str = Field(
        default="qwen2.5:3b",
        description="Kleines Modell für Mehr-Aspekt-Query-Zerlegung (Pre-Retrieval)",
    )
    translate_enabled: bool = True
    decompose_enabled: bool = True
    # Interim-Streaming-Batching (Bus-Last vs. gefühlte Latenz)
    llm_flush_chars: int = 80
    llm_flush_secs: float = 0.15
    # Antwort-Tiefe: mehr/längere Quellen im Prompt → ausführlichere Synthese,
    # aber längerer Prompt (langsamer, mehr Kontext). Richtung "C" (tief).
    llm_doc_chars: int = Field(default=1500, description="Zeichen pro Vault-Quelle im Prompt")
    llm_top_docs: int = Field(default=8, description="Anzahl Quellen im Prompt")
    llm_web_chars: int = Field(default=1500, description="Zeichen pro Web-Treffer im Prompt")
    llm_web_max: int = Field(default=8, description="max. Web-Treffer im Prompt")
    llm_num_ctx: int = Field(
        default=16384,
        description="Ollama-Kontextfenster (Tokens). WICHTIG: Ollama-Default ist "
                    "nur 4096 → tiefe Prompts würden STILL abgeschnitten. 16384 "
                    "passt qwen2.5:7b GPU-only in 8GB (verifiziert, 6.3 GB).")
    llm_prompt_char_budget: int = Field(
        default=40000,
        description="Sicherheits-Cap für die Prompt-Länge (Zeichen); darüber "
                    "werden die Vault-Quellen gekürzt (~13k Tokens < num_ctx).")
    rewrite_enabled: bool = Field(
        default=True,
        description="Referenzielle Folgefragen vor dem Retrieval zu "
                    "eigenständigen Fragen umformen (Follow-up-Retrieval)",
    )
    rewrite_model: str = Field(
        default="",
        description="Modell fürs Query-Rewrite; leer = decompose_model",
    )

    # ── Embeddings ──────────────────────────────────────────────────────
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    embedding_device: Literal["cpu", "cuda"] = Field(
        default="cpu",
        description="cpu default: MiniLM ist auf CPU schnell genug (~20ms/Query), "
                    "und die 8GB VRAM gehören vollständig der LLM",
    )

    # ── Retrieval-Verhalten ─────────────────────────────────────────────
    retrieval_deterministic: bool = Field(
        default=True,
        description="Reproduzierbares Scoring: Thompson-Posterior-MEAN statt "
                    "Sampling, statische Gewichte statt Entropie-Modulation, "
                    "Warp aus (reine Cosine-Basis). Benchmark-Befund: mit "
                    "Sampling schwankte dieselbe Query um bis zu 24 Distanz-"
                    "punkte und Nonsens erreichte TRUST. False = Chaos-Modus "
                    "des Alt-Systems.",
    )
    lexical_rerank_boost: float = Field(
        default=10.0,
        description="Reorder der Top-Treffer nach Query-Term-Überlappung "
                    "(Distanz-Punkte Bonus bei voller Überlappung; 0 = aus). "
                    "Ändert nur die Reihenfolge, nie Distanzen/Zonen.",
    )

    # ── Routing & Antwortlogik ──────────────────────────────────────────
    code_route_high: float = 0.40
    code_route_low: float = 0.25
    # Zonen-Schwellen — rekalibriert auf das deterministische Scoring
    # (Benchmark 2026-07: relevante Queries 17–47, Nonsens ab ~51; die
    # Alt-System-Werte 55/68 galten für das entropie-modulierte Scoring).
    vault_trust_threshold: float = 50.0
    vault_soft_max_distance: float = 62.0
    code_vault_trust_threshold: float = 57.0

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
        if self.workflow_repo is None:
            self.workflow_repo = self.executor_workspace / "repo"


settings = CollectSettings()
