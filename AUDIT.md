# collect2 · hótr̥ — System-Audit / Stand der Dinge

**Stand:** 2026-07-06 · **Repo:** `~/collect2` + privates Remote `jnrabit/collect2` (CI grün) · **Branch:** `main`, 19 Commits
**Zweck dieses Dokuments:** Vollständiger Überblick für eine neue Session/Instanz ohne Vorkontext.

---

## 1. Was ist collect2?

Lokal-first **Multi-Agent-Wissenssystem** mit Redis-Backbone: beantwortet Wissensfragen
geerdet auf zwei Dokument-Vaults (259k General + 1,5k Code), führt Multi-Step-Pläne aus
(Dateioperationen) und erledigt Code-Aufgaben (generieren → testen → committen).
Bedienung über Web-Chat, REPL, CLI und REST-API. Anzeigename **hótr̥** (vedisch, होतृ),
technischer Name `collect`.

**Herkunft:** Kompletter Neuaufbau (Juli 2026) aus zwei Vorgängern — `~/collect`
(Redis-Multi-Agent, 55k LOC, gewachsen/fragil) und `~/vibelike` (In-Process,
27k LOC, gutes Sicherheitsnetz, drei Monolithe). Design-Anker: **DESIGN.md**
(Grundsatz-Entscheidungen, Port-Matrix, 10 Design-Regeln, Phasenplan).
Beide Alt-Systeme existieren unangetastet als Referenz; das alte collect
läuft noch parallel (eigene Redis-Channels, kein Konflikt).

**Grundsatz-Entscheidungen (User):** Redis-Multi-Agent (Parallelität/Speed) ·
neues Repo statt In-Place-Umbau · lokal-first (Ollama), Cloud später optional.

## 2. Zahlen

| | |
|---|---|
| Eigener Code | ~5.700 LOC in `src/collect/` (größtes Modul < 260 LOC) |
| Tests | **196 passed** (`pytest`), plus Integrationstests gegen echte Vaults; CI grün (157 passed / 6 skipped ohne Daten) |
| Agenten | 10 (ein Prozess, ein Bus-Thread pro Agent) |
| Vaults | General 258.991 Docs (harvestbar) · Code 1.469 Docs · Caches ~416 MB, 384-dim |
| Daten | `~/collect2/data/` (migriert, SHA256-verifiziert; Alt-Daten unberührt) |
| Python | 3.12 (gepinnt — 3.14 brach protobuf im Alt-Stack), venv `.venv` |
| Modelle | qwen2.5:7b (Chat/Synthese, GPU-only ~46 tok/s) · qwen2.5-coder:7b (Code-Workflow) · gemma2:2b (DE→EN) · qwen2.5:3b (Query-Zerlegung) |
| Hardware | AMD RX 7600 8GB VRAM; Embeddings (MiniLM multilingual) bewusst auf CPU |

## 3. Architektur

```
user_query ─► Orchestrator ── Translate (DE→EN) · Route (Code-Centroid) · Decompose
                │             + Contribution-MANIFEST an ResponseAgent
                ├─ Code-Task? ──► WorkflowAgent (Briefing→Plan→Exec→Verify→Commit)
                ├─ Plan-Query? ─► PlanningAgent ↔ DecisionAgent ↔ ExecutorAgent
                └─ Wissensfrage:
                     RetrievalAgent (General) ─┐  parallel
                     CodeRetrievalAgent ───────┤
                     LLMAgent (wartet auf Beiträge, streamt Tokens) ─┘
                ResponseAgent: finalisiert bei Manifest-Vollständigkeit ODER Deadline
                     → Drei-Zonen-Synthese → user_response
                LearningAgent: Triplet-Log + Fakt-Extraktion (nur TRUST) → Ossifikat-Staging
```

**Transport** (`bus.py`): Message-Umschlag mit `message_id` (Idempotenz), `RedisBus`
(Produktion) + `InMemoryBus` (synchron, für Tests ohne Redis). Channel-Prefix `c2.`.
**Kernregeln** (DESIGN §5): Logik von Transport getrennt (jeder Agent ohne Redis testbar),
kein Blocking-Wait (Zustandsmaschinen), Finalisierung explizit per Manifest statt
Timeout-Heuristik, ein Embedding-Backend, Module < 400 LOC.

## 4. Retrieval (Kern)

- **Engine:** ChaosRetrieval (Port aus vibelike) über In-RAM-Matrix; Vault-Format
  Chaos-XOR+LZMA, Cipher in reinem numpy (byte-kompatibel zu Alt-Vaults, numba entfällt).
  C++ `libquelibrium.so` vendored (Hardware-State; Shadow-Mode ohne .so).
- **Deterministischer Modus (default):** Thompson-Posterior-Mean statt Sampling,
  statische cosine-dominante Gewichte (0.8/0.05/0.1/0.05), Warp aus. Resonanz-Lern-Boost
  gesättigt + gecappt (nudgt, teleportiert nie über Zonen). Chaos-Modus per Flag.
- **Pipeline:** [Follow-up-Rewrite bei referenziellen Folgefragen] → Translate →
  Centroid-Routing (general/both/code) → Decompose (Mehr-Aspekt) → Suche pro
  Teilfrage → RRF-Fusion → **lexikalisches Rerank** (Query-Term-Überlappung,
  reorder-only) → Drei-Zonen-Verdikt.
- **Follow-up-Rewrite:** deterministisches Gate (kurz + Pronomen/Deixis) →
  qwen2.5:3b formt aus den letzten Turns eine eigenständige Frage; Original
  läuft als Fusions-Subquery mit (nie schlechter als Status quo). Gemessen:
  referenzielle Folgefragen 0/6 → 5/6 relevant.
- **Harvest:** `collect-harvest wikipedia <topic> --limit N` — höflicher
  MediaWiki-Adapter + sicherer Ingest (Qualität/Dedupe → Backup → tmp →
  Reload-Verify → atomarer Move). HTTP/Netzwerk-Lücke damit geschlossen.
- **Drei Zonen:** TRUST (≤50) volle Antwort · GRAUZONE (50–62) Antwort mit Warnhinweis ·
  FALLBACK (≥62) LLM unterdrückt (Halluzinations-Schutz). Verbürgte Fakten heben
  FALLBACK auf. Code-Vault-TRUST: 57.
- **Messlatte:** `scripts/retrieval_benchmark.py` (9 Queries × N Repeats).
  Stand: Zonen stabil 9/9, antwortbar-korrekt 9/9, max ΔDist 4,6 (Baseline davor:
  3/9, 7/9, 24,6 — Nonsens erreichte TRUST).

## 5. Grounding & Lernen (Ossifikat)

Triple-Store als git-Submodule (`github.com/jnrabit/ossifikat`), DB `data/ossifikat.db`.
**Schleife:** TRUST-Antworten → LLM-Extraktion → **Staging** (unbestätigt) →
menschliches Review (`/review` im REPL) → **verbürgt** → verbürgte Fakten werden
künftigen Antworten autoritativ vorangestellt (Vorrang vor Vault-Quellen bei
Widerspruch). Grauzone/Fallback/Pläne ossifizieren nie. Zusätzlich Triplet-Log
(`logs/triplets.jsonl`, SHA256-IDs) für jede Antwort.

## 6. Code-Workflow (Phase 6)

`collect.workflow`: **Briefing** (deterministisch: Repo-Baum + relevante Dateien) →
**Planning** (LLM → Datei-Plan, Pfad-Hygiene) → **Execution** (pro Datei; bereits
geschriebene Dateien wandern in Folge-Prompts) → **Verify** (pytest, begrenzt auf
`workspace/repo`) → **Commit nur bei grün**. Gates vor jedem Write: Syntax +
Regression-Guard + Security-Scan. Bis 2 Reparatur-Runden (Verify-Fehler → LLM
entscheidet: Implementierung vs. Test-Erwartung fixen).
Trigger: `code:`-Prefix oder Implementier-Verb + Code-Objekt.
**Real:** einfache Tasks (fib+Tests) E2E grün in ~9s inkl. git-Commit; string-exakte
Tasks (slugify) scheitern an 7B-Erwartungstreue → ehrlich rot, kein Commit.

## 7. Oberflächen & Betrieb

- **Web-Chat** `http://127.0.0.1:8767/chat`: Token-Streaming (erstes Token ~1,4s warm),
  Live-Token-Zähler, Meta-Zeile (Zone/Distance/`⚡ n tok @ x tok/s`/Fakten),
  Markdown (marked+DOMPurify) + LaTeX (KaTeX, mathe-geschützt vorm Markdown-Parser),
  Gesprächskontext pro Verbindung (letzte 5 Turns, nur in die Synthese).
- **CLIs:** `collect-agents` (Stack) · `collect-ask` · `collect-repl` (Fragen,
  `/status`, `/review`, `/facts`) · `collect-api` · `collect-harvest` ·
  `collect-doctor` · `collect-guard`.
- **API-Härtung** (`api_security.py`): Default localhost + offen (Zero-Config);
  mit `COLLECT_API_TOKEN` wird Bearer/`?token=`-Auth auf `/api/query`,
  `/api/facts`, `/ws/chat` erzwungen (`/health`, `/chat` bleiben öffentlich).
  In-Process-Rate-Limit (2 Tiers, 429+Retry-After), CORS-Allowlist,
  Security-Header, Body-Cap, generische Fehler (kein Leak). **Exposure ist
  Opt-in:** Nicht-localhost-Bind ohne Token → Preflight verweigert den Start.
  **Vor echter Exposure noch nötig:** TLS terminiert der Reverse-Proxy (nicht
  die App); optional CSP für die CDN-Skripte (KaTeX/marked).
- **Betrieb:** `scripts/start.sh [--api]` (Preflight Redis/Ollama, wartet auf online),
  `stop.sh`, `status.sh` (Heartbeat-Hash in Redis); systemd-User-Units in `deploy/`.
  Konfiguration: `.env` (Prefix `COLLECT_`, alle Optionen in `.env.example`);
  aktuell gesetzt: `COLLECT_DATA_DIR`, `COLLECT_CODE_MODEL=qwen2.5-coder:7b`.

## 8. Sicherheitsnetz

- `collect-doctor` (Syntax · Config-Import-Auflösung · Kern-Imports · Regression) —
  grün; `--fast` als CI-Gate.
- `collect-guard` / Pre-Commit-Hook: blockt unautorisierten Symbol-Verlust +
  Datei-Kollaps (Escape: `--no-verify`, dokumentiert im Commit).
- `validation.py`: Security-Patterns (eval/exec, hardcoded creds, SQL-f-Strings …) —
  blockt Writes in Executor + Workflow.
- CI-Workflow liegt in `.github/` bereit (kein Remote → läuft noch nie).
- Executor-Grenzen: Lesen nur unter `~/collect2`+`~/collect`, Schreiben nur im
  Workspace, `execute:` default aus.

## 9. Bekannte Grenzen & offene Punkte

| Thema | Stand |
|---|---|
| 7B-Modellgrenze im Code-Workflow | string-exakte Tests inkonsistent; Hebel: größeres Coder-Modell, mehr Reparatur-Runden, Cloud-Fallback (DESIGN §2) |
| Embedder-Relevanz | MiniLM verwechselt Wortfelder (TLS↔soziales Handshaking); Rerank mildert, Top-1 nicht immer ideal. HTTP-Lücke per Harvest geschlossen |
| API-Exposure | Schutzschicht steht (Token/Rate/CORS/Header/Preflight); vor echter Exposure noch: TLS am Reverse-Proxy, optional CSP |
| Cutover offen | Alt-Stack `~/collect` läuft parallel weiter — Stoppen/Archivieren ist User-Entscheidung |
| Harvest v1 | nur Wikipedia-Adapter; ArXiv/OpenAlex/RFC als Ausbau. Kein Daemon/Scheduler (manueller Lauf) |
| Idiom-System / Sandbox / validator2-Vollport | bewusst zurückgestellt (Anti-Scaffold-Regel); Consumer (Workflow) existiert jetzt |
| Follow-up-Retrieval | Kontext nur in Synthese; kurze referenzielle Fragen retrieven schwach (Query-Rewrite wäre Ausbau) |

## 10. Verzeichnis-Karte

```
src/collect/
  bus.py, config.py, client.py, status.py, api.py, repl.py
  doctor.py, regression_guard.py, validation.py
  agents/    base, orchestrator, retrieval, llm, decision, planning,
             executor, response, learning, workflow, runner, ollama
  retrieval/ vault, native(+lib/*.so), store, chaos, resonance,
             embedding, router, zones, translator, decomposer, service
  grounding/ facts, triplets
  workflow/  context, briefing, planning, execution, verify, engine
  harvest/   wikipedia, ingest, cli
  retrieval/ … + rewriter (Follow-up-Query-Rewrite)
  web/chat.html
scripts/   start|stop|status.sh, migrate_data.py, retrieval_benchmark.py
deploy/    systemd-User-Units · ossifikat/ (Submodule) · tests/ (163)
data/      Vaults+Caches (468 MB, gitignored) · workspace/repo (Workflow-Ziel)
```

**Historie (15 Commits):** Phase 0+1 Skelett/Sicherheitsnetz → 2 Retrieval-Kern →
3 Redis-Orchestrierung → 4 Grounding → 5 Betrieb/Migration → Web-Chat/Streaming/
KaTeX/Markdown → Retrieval-Qualität → Gesprächskontext → 6 Code-Workflow.
