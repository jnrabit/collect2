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
        default=300.0,
        description="Deadline (s) für die Manifest-Finalisierung im ResponseAgent",
    )
    plan_deadline: float = Field(
        default=300.0,
        description="Deadline (s) für Plan-Queries (Kaskade braucht länger)",
    )
    llm_timeout: float = 360.0
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
        default_factory=lambda: [
            Path.home() / "projekte",
            Path.home() / "collect2",
        ],
        description="Allowlist für Ad-hoc-Datei-Lesen. Enthält ~/projekte/ + ~/collect2/ — "
                    "bewusst Opt-in pro Projekt, nie / root.",
    )
    file_direct_threshold: int = Field(
        default=8192, description="Datei < N Bytes → ganz in den Prompt (kein Chunking)")
    file_max_bytes: int = Field(
        default=2_000_000, description="max. Gesamtbytes über alle gelesenen Dateien")
    file_max_files: int = Field(
        default=25, description="max. Dateien bei Verzeichnis-Lesen")
    file_max_depth: int = Field(default=3, description="max. Verzeichnistiefe")
    file_chunk_chars: int = Field(default=600, description="Chunk-Größe (Zeichen)")
    file_top_chunks: int = Field(default=2, description="relevanteste Chunks in die Synthese")
    file_extensions: str = Field(
        default=".py,.js,.ts,.go,.rs,.java,.c,.h,.cpp,.sh,.md,.txt,.rst,"
                ".toml,.yaml,.yml,.json,.cfg,.ini",
        description="Endungs-Allowlist (CSV) fürs Verzeichnis-/Local-Lesen")

    # ── Code-Workflow (Phase 6) ─────────────────────────────────────────
    workflow_enabled: bool = Field(
        default=True,
        description="Code-Workflow-Erkennung (is_code_task): aus = Implementier-"
                    "Anfragen laufen als normale Wissensfrage durchs Retrieval "
                    "statt in die Code-Generierungs-Kaskade.",
    )
    planning_enabled: bool = Field(
        default=True,
        description="Plan-Erkennung (is_plan_query): aus = Plan-Vokabular "
                    "startet keine Planungs-Kaskade mehr.",
    )
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
    web_search_auto_with_file: bool = Field(
        default=False,
        description="Auto-Web auch wenn Datei-Kontext die Antwort bereits "
                    "erdet (default: Datei-Kontext unterdrückt Auto-Web).",
    )
    web_search_url: str = Field(
        default="http://localhost:8888",
        description="SearXNG-Instanz (selbst-gehostet, keine Cloud-Abhängigkeit)",
    )
    web_search_timeout: float = 15.0
    web_search_results: int = Field(
        default=5, description="max Suchergebnisse von SearXNG")
    web_search_max_snippets: int = Field(
        default=6, description="max Snippets im Web-Beitrag (vor Page-Chunks)")
    web_search_snippet_chars: int = Field(
        default=1200, description="Zeichen pro Snippet/Page-Chunk im Beitrag")
    web_search_page_fetches: int = Field(
        default=2, description="Top-N Ergebnisse als ganze Seite abrufen")
    web_search_page_chunk_chars: int = Field(
        default=1000, description="Chunk-Größe beim Zerlegen abgerufener Seiten")
    web_search_page_top_chunks: int = Field(
        default=3, description="relevanteste Seiten-Chunks (Cosine) pro Seite")
    web_search_trigger: str = Field(
        default="recherchiere|recherchieren|such im web|suche im web|google|"
                "web suche|web recherche|im internet|online suche",
        description="Regex-Trigger für explizite Web-Recherche-Anfrage (case-insensitiv)")

    # ── Qwythos-Serving (separater K4N0N3-Auftrag, Default aus) ──────────
    qwythos_enabled: bool = Field(
        default=False,
        description="Qwythos-Modell-getriebener Agent statt Pipeline. "
                    "Erfordert ein Tool-Calling-fähiges Modell in Ollama "
                    "(/api/chat mit tools). Gehört zum K4N0N3-Serving-Auftrag.")

    # ── K4N0N3-Integration ──────────────────────────────────────────────
    k4n0n3_enabled: bool = Field(
        default=False,
        description="K4N0N3-Adapter als generate_fn nutzen. Konfiguriert "
                    "Stop-Tokens und Parameter für Qwythos. Erfordert das "
                    "Qwythos-Modell in Ollama. Bei true wird k4n0n3.generate "
                    "statt ollama.generate in alle Agenten injiziert.")
    k4n0n3_model: str = Field(
        default="pdurlej/qwythos-9b-claude-mythos-5-1m",
        description="Modell-Name in Ollama für K4N0N3-Adapter. "
                    "Default: Qwythos-9B mit Claude-Mythos-Finetune.")
    k4n0n3_rewrite_model: str = Field(
        default="",
        description="Modell für Query-Rewrite via K4N0N3-Adapter. "
                    "Leer = k4n0n3_model. Ein separates kleineres Modell "
                    "kann den Qwythos-Adapter für Rewrite-Aufgaben nutzen.")
    rewrite_k4n0n3_enabled: bool = Field(
        default=False,
        description="NUR den Query-Rewriter in-process über den K4N0N3-Adapter "
                    "fahren (nicht den ganzen Agenten-Stack wie k4n0n3_enabled). "
                    "Für den finetunten Qwythos-Rewriter, der über Ollama auf "
                    "dieser Hardware nicht läuft (qwen3_5 braucht MLX). Das "
                    "Modell kommt aus k4n0n3_rewrite_model (z. B. der Pfad zum "
                    "gemergten Adapter-Modell). Single-Shot, daher trägt die "
                    "Sekunden-pro-Token-Latenz des Offload-Pfads hier.")

    # ── Trace-Pipeline (Trainingsdaten-Sammlung, CPU-only) ───────────────
    traces_enabled: bool = Field(
        default=True,
        description="Trace-Collector: Modellaufrufe als Trainings-Traces "
                    "aufzeichnen (rotierend data/traces/YYYY-MM-DD.jsonl)")
    traces_dir: Path = Field(
        default_factory=lambda: Path.home() / "collect2" / "data" / "traces",
        description="Verzeichnis für die rotierenden Trace-JSONL-Dateien")
    traces_seq_len: int = Field(
        default=2048,
        description="Ziel-seq_len für den Validator-Grenzwert (Längen-Report "
                    "entscheidet 1024 vs. 2048 im Trainings-Auftrag)")
    traces_tokenizer: str = Field(
        default="Qwen/Qwen2.5-7B-Instruct",
        description="Tokenizer für den Template-Render-Check. MUSS zum "
                    "Inferenzmodell passen (Qwythos = Qwen3.5) — sonst laufen "
                    "Trainings- und Inferenz-Template auseinander. Als HF-Repo "
                    "oder lokaler Pfad; Fallback nur mit klarer Meldung.")

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
        default="qwen3:8b",
        description="Generalist für Q&A-Synthese. qwen3:8b per Messung "
                    "(BASISMODELL_BERICHT, 2026-07-29): 20/24 auf eval_hard, "
                    "passt in 8GB VRAM, verdrängt den 7b sauber von der GPU.",
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
        default="qwen3:8b",
        description="Modell für Mehr-Aspekt-Query-Zerlegung (Pre-Retrieval). "
                    "qwen3:8b statt qwen2.5:3b: bessere Subquery-Qualität, "
                    "kein separates Modell im VRAM (main=rewrite=decompose=8b).",
    )
    translate_enabled: bool = True
    decompose_enabled: bool = True
    decompose_max_subqueries: int = Field(
        default=3, description="max Teilfragen pro Query (RRF-Fusion)")
    # Interim-Streaming-Batching (Bus-Last vs. gefühlte Latenz)
    llm_flush_chars: int = 80
    llm_flush_secs: float = 0.15
    # Antwort-Tiefe: mehr/längere Quellen im Prompt → ausführlichere Synthese,
    # aber längerer Prompt (langsamer, mehr Kontext). Richtung "C" (tief).
    llm_doc_chars: int = Field(default=600, description="Zeichen pro Vault-Quelle im Prompt")
    llm_top_docs: int = Field(default=4, description="Anzahl Quellen im Prompt")
    llm_num_predict: int = Field(
        default=2048,
        description="Ollama num_predict: max generierte Tokens pro Antwort "
                    "(Sicherheitsnetz gegen Weglaufen). 1024 schnitt tiefe "
                    "Antworten (Richtung C, 8 Quellen) mitten im Satz ab; "
                    "2048 passt mit dem 40k-Prompt-Budget in num_ctx=16384.")
    retrieval_max_hits: int = Field(
        default=8, description="Hits pro Vault im Bus-Beitrag (Basis für llm_top_docs)")
    retrieval_max_content_chars: int = Field(
        default=1600,
        description="Zeichen pro Hit auf dem Bus. MUSS ≥ llm_doc_chars sein — "
                    "sonst kappt der Bus die Quellen, BEVOR der LLM sie sieht "
                    "(der alte Wert 1200 hat llm_doc_chars=1500 still kastriert).")
    llm_web_chars: int = Field(default=1500, description="Zeichen pro Web-Treffer im Prompt")
    llm_web_max: int = Field(default=8, description="max. Web-Treffer im Prompt")
    llm_num_ctx: int = Field(
        default=16384,
        description="Ollama-Kontextfenster (Tokens). 16k passt in 8GB VRAM mit qwen3:8b "
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
        default="qwen3:8b",
        description="Modell fürs Query-Rewrite; leer = decompose_model. "
                    "qwen3:8b per Messung (BASISMODELL_BERICHT, 2026-07-29): "
                    "20/24 gegen 15/24 von qwen2.5:3b auf demselben Treiber — "
                    "und im echten Wechselbetrieb mit dem 7b-Antwortmodell "
                    "SCHNELLER (12,1 s statt 21,2 s pro Folgefragen-Turn): der "
                    "3b bleibt zwar geladen, bricht neben dem 7b aber auf "
                    "~1 tok/s ein, der 8b verdrängt ihn sauber und rechnet "
                    "voll auf der GPU.",
    )
    rewrite_max_content_terms: int = Field(
        default=6,
        description="Referential-Gate: mehr Inhaltswörter → Frage gilt als "
                    "eigenständig (kein Rewrite, keine History-Anbindung)")

    # ── Embeddings ──────────────────────────────────────────────────────
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    embedding_device: Literal["cpu", "cuda"] = Field(
        default="cpu",
        description="cpu default: MiniLM ist auf CPU schnell genug (~20ms/Query), "
                    "und die 8GB VRAM gehören vollständig der LLM",
    )

    # ── Prompts ─────────────────────────────────────────────────────────
    prompts_dir: Optional[Path] = Field(
        default=None,
        description="Verzeichnis mit Prompt-Overrides (<name>.txt). Leer = "
                    "eingebaute Prompts. Vorlagen exportieren: "
                    "python -m collect.prompts export <dir>. Invalide Dateien "
                    "(fehlende/unbekannte Platzhalter) fallen mit Warnung auf "
                    "den eingebauten Prompt zurück.",
    )

    patterns_dir: Optional[Path] = Field(
        default=None,
        description="Verzeichnis mit Pattern-Overrides (<name>.json): "
                    "plan_detection, code_detection, profile_signals. "
                    "Vorlagen: python -m collect.patterns export <dir>. "
                    "Invalide Dateien fallen mit Warnung auf die eingebauten "
                    "Listen zurück. Security-Patterns sind bewusst NICHT "
                    "überschreibbar.",
    )

    # ── Retrieval-Verhalten ─────────────────────────────────────────────
    retrieval_profile: str = Field(
        default="chaos",
        description="Retrieval-Profil: chaos (Lorenz-Warp + Thompson + Resonance), "
                    "balanced, broad, resonant, precise, adaptive, auto. "
                    "Prefix-Override im Query-Text ([precise] etc.) hat Vorrang.",
    )
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

    # ── Retrieval-Scoring: Rohwerte (das "fließende Einstellen") ────────
    # Score = α·cosine + β·thompson + γ·resonanz + δ·exploration.
    # Basis-Gewichte gelten ohne Profil UND als 'balanced'-Preset; die
    # chaos_*-Gewichte sind das 'chaos'-Preset / der nicht-deterministische
    # Modus. ACHTUNG: Die Zonen-Schwellen (50/62) sind auf die Basis-Gewichte
    # kalibriert — wer α stark ändert, verschiebt die Zonen-Semantik.
    retrieval_alpha: float = Field(default=0.80, description="Gewicht Cosine-Ähnlichkeit")
    retrieval_beta: float = Field(default=0.05, description="Gewicht Thompson-Posterior")
    retrieval_gamma: float = Field(default=0.10, description="Gewicht Resonanz-Boost")
    retrieval_delta: float = Field(default=0.05, description="Gewicht Explorations-Bonus")
    retrieval_chaos_alpha: float = Field(default=0.50, description="α im Chaos-Modus")
    retrieval_chaos_beta: float = Field(default=0.20, description="β im Chaos-Modus")
    retrieval_chaos_gamma: float = Field(default=0.20, description="γ im Chaos-Modus")
    retrieval_chaos_delta: float = Field(default=0.10, description="δ im Chaos-Modus")
    retrieval_rrf_k: int = Field(
        default=60,
        description="Reciprocal-Rank-Fusion-Konstante k (Subquery-Fusion). "
                    "Kleiner = Top-Ränge dominieren stärker.")
    retrieval_no_hit_distance: float = Field(
        default=999.0,
        description="Distanz-Konvention für 'keine Treffer' (→ FALLBACK).")
    retrieval_adapt_interval: int = Field(
        default=50,
        description="Alle N Suchen: Lorenz-Parameter-Adaption an den "
                    "Resonanzfeld-Zustand.")
    retrieval_resonance_anchors: int = Field(
        default=20,
        description="Anzahl Top-Treffer als Anker für den Resonanz-Boost.")
    retrieval_adaptive_explore_until: int = Field(
        default=20,
        description="adaptive-Profil: bis zu dieser Such-Anzahl explorativ "
                    "(breite Gewichte + Sampling).")
    retrieval_adaptive_settle_until: int = Field(
        default=80,
        description="adaptive-Profil: bis hierhin Übergangsphase, danach "
                    "präzise Gewichte. Zählt pro Service-Prozess.")
    retrieval_entropy_mix: tuple[float, float, float, float] = Field(
        default=(0.3, 0.1, 0.1, 0.05),
        description="Chaos-Modus: Mix-Anteile (α,β,γ,δ) der Entropie-"
                    "Modulation. Env als JSON: [0.3,0.1,0.1,0.05]")
    resonance_decay: float = Field(
        default=0.995,
        description="Zerfallsrate der Ko-Aktivierungsmatrix pro Update "
                    "(näher an 1.0 = längeres Gedächtnis).")
    retrieval_profile_overrides: dict = Field(
        default_factory=dict,
        description="Gezielte Überschreibung einzelner Profil-Werte, gemergt "
                    "über die Presets. Env als JSON: "
                    '{"chaos": {"delta": 0.2}, "broad": {"alpha": 0.6}}')

    # ── Routing & Antwortlogik ──────────────────────────────────────────
    code_route_high: float = 0.40
    code_route_low: float = 0.25
    # Zonen-Schwellen — kalibriert auf das deterministische Scoring (Benchmark
    # 2026-07: relevante Queries 17–47, Nonsens ab ~51). Der Chaos-Modus
    # (Lorenz-Warp) verschiebt Distanzen nach UNTEN (bessere Matches) —
    # daher hier kein höherer Wert nötig; der Warp liefert die Exploration.
    vault_trust_threshold: float = 50.0
    vault_soft_max_distance: float = 62.0
    code_vault_trust_threshold: float = 57.0
    fallback_suppress: bool = Field(
        default=True,
        description="Halluzinations-Schutz: FALLBACK-Zone ohne Erdung (Fakten/"
                    "Datei/Web) → Antwort unterdrücken. False = LLM antwortet "
                    "trotzdem, mit deutlichem Nicht-geerdet-Hinweis.",
    )

    # ── Beobachter (SilentObserver, Autopilot, OsmosisObserver, DreamCycle) ──
    observe_silent_interval: int = Field(
        default=60, description="SilentObserver: Sekunden zwischen Phantom/Void/Drift-Scans")
    observe_osmosis_enabled: bool = Field(
        default=True, description="OsmosisObserver beim Start aktivieren")
    observe_osmosis_interval: int = Field(
        default=30, description="OsmosisObserver: Sekunden zwischen Topologie-Scans")
    observe_autopilot_enabled: bool = Field(
        default=True, description="Autopilot beim Start aktivieren")
    observe_autopilot_interval: int = Field(
        default=30, description="Autopilot: Sekunden zwischen Query-Generierungen")
    observe_autopilot_cycles: int = Field(
        default=500, description="Autopilot: max Zyklen (0=endlos)")

    # ── Timeouts ────────────────────────────────────────────────────────
    query_timeout: float = 360.0
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


    @property
    def retrieval_profiles(self) -> dict:
        """Die 6 benannten Profile — α(cosine), β(thompson), γ(resonance),
        δ(exploration), warp(Bool), sampling(Bool). 'sampling' steuert ob
        Thompson einen Zufallswert oder den Posterior-Mean liefert.
        'balanced' und 'chaos' speisen sich aus den Rohwert-Feldern oben;
        retrieval_profile_overrides mergt gezielt einzelne Werte darüber."""
        profiles = {
            "precise":  {"alpha": 0.95, "beta": 0.0,  "gamma": 0.0,  "delta": 0.05,
                         "warp": False, "sampling": False},
            "balanced": {"alpha": self.retrieval_alpha, "beta": self.retrieval_beta,
                         "gamma": self.retrieval_gamma, "delta": self.retrieval_delta,
                         "warp": False, "sampling": False},
            "broad":    {"alpha": 0.55, "beta": 0.15, "gamma": 0.10, "delta": 0.20,
                         "warp": False, "sampling": True},
            "resonant": {"alpha": 0.60, "beta": 0.05, "gamma": 0.30, "delta": 0.05,
                         "warp": False, "sampling": False},
            "chaos":    {"alpha": self.retrieval_chaos_alpha,
                         "beta": self.retrieval_chaos_beta,
                         "gamma": self.retrieval_chaos_gamma,
                         "delta": self.retrieval_chaos_delta,
                         "warp": True,  "sampling": True},
            "adaptive": {"alpha": 0.0,  "beta": 0.0,  "gamma": 0.0,  "delta": 0.0,
                         "warp": False, "sampling": False,
                         "adaptive": True},
        }
        # Overrides: nur bekannte Profile & bekannte Keys — Tippfehler in der
        # Env dürfen keine stillen Geister-Keys erzeugen.
        for name, ov in (self.retrieval_profile_overrides or {}).items():
            if name in profiles and isinstance(ov, dict):
                profiles[name].update(
                    {k: v for k, v in ov.items() if k in profiles[name]})
        return profiles

    def get_profile(self, name: str) -> dict:
        """Validierten Profil-Lookup — 'auto' oder ungültig → balanced."""
        profiles = self.retrieval_profiles
        if name in profiles:
            return profiles[name]
        return profiles["balanced"]


settings = CollectSettings()
