# collect2 — Design-Anker

**Stand:** 2026-07-02 · Dieses Dokument ist der verbindliche Anker für den
Neuaufbau. Bei Zielkonflikten während der Implementierung gilt: erst hier
nachschlagen, dann entscheiden — und Abweichungen hier dokumentieren.

---

## 1. Ziel

Neuaufbau von `collect` (Multi-Agent-Wissenssystem) von Grund auf, unter
Mitnahme von „allem Guten" aus zwei Vorgängern:

- **`~/collect`** — Redis-Multi-Agent-System, 18 Agenten, ~55k LOC.
  Stärken: die Daten (General-Vault 259.518 Docs, Code-Vault 1.733 Docs),
  Quelibrium-Retrieval-Engine, Drei-Zonen-Antwortlogik, Centroid-Code-Routing,
  Plan→Decide→Act-Semantik, Betriebsskripte. Schwächen: gewachsene Struktur,
  Pub/Sub-Races, env-Spaghetti.
- **`~/vibelike`** — In-Process-Nachfolger, ~27k LOC, CI-grün.
  Stärken: deterministisches Sicherheitsnetz (doctor, regression_guard,
  validator2, CI), Ossifikat-Grounding, Idiom-System, Query-Vorverarbeitung,
  Pydantic-Config, sandbox/reqqueue/logdb. Schwächen: drei untestbare
  Monolithen, unverdrahtete Scaffolds, Import-Fragilität.

## 2. Getroffene Grundsatz-Entscheidungen (User, 2026-07-02)

1. **Architektur: Redis-Multi-Agent.** Begründung: Geschwindigkeit durch echte
   Parallelität (Retrieval, LLM, Decision gleichzeitig), langfristig tragfähiger
   als serieller In-Process-Dispatch. Die bekannten Pub/Sub-Kosten (Races,
   Timeout-Heuristiken) werden durch Design-Regeln (§5) adressiert, nicht durch
   Architekturwechsel.
2. **Neues Repo** (`~/collect2`), nicht In-Place-Umbau. Altes `~/collect` bleibt
   als Referenz + Daten-Substrat, bis der Cutover abgeschlossen ist.
3. **Modelle: lokal-first** (Ollama, `qwen2.5:7b`, GPU-only auf RX 7600 8GB).
   Cloud-APIs später als optionaler Fallback, nie als Voraussetzung.

## 3. Zielarchitektur (Grobbild)

```
user_query ──► Orchestrator ── Klassifikation (Centroid-Routing + Plan-Erkennung)
                  │
                  ├─► RetrievalAgent (General-Vault, Quelibrium/ChaosRetrieval)
                  ├─► CodeRetrievalAgent (Code-Vault)          ── parallel ──
                  ├─► LLMAgent (Ollama, Vault-Kontext)                │
                  ├─► DecisionAgent (Erfahrungen, Plan-Decompose)     │
                  │                                                   ▼
                  └─► PlanningAgent (Plan→Decide→Act, exklusiver Pfad)
                                                                      │
              ResponseAgent ◄─────────── sammelt & finalisiert ◄──────┘
                  │   Drei-Zonen-Logik (TRUST / GRAUZONE / FALLBACK)
                  ▼
             user_response
```

**Bewusst weniger Agenten als die 18 im Alt-System.** Startmenge: orchestrator,
retrieval, code-retrieval, llm, decision, planning, executor, response,
monitoring. Alles Weitere (learning, debate, kg, …) erst, wenn der Kern steht
und ein konkreter Bedarf besteht.

**Grounding-Schicht (Phase 4):** Ossifikat-Triple-Store — verbürgte Fakten
haben Vorrang vor Vault-Treffern. Idiom-System für Code-Aufgaben.

## 4. Was woher portiert wird

| Baustein | Quelle | Anmerkung |
|---|---|---|
| doctor, regression_guard, CI | vibelike | Phase 1, nahezu 1:1 |
| Pydantic-Settings (`COLLECT_`-Prefix) | vibelike (Muster) | Phase 1, neu geschrieben |
| Quelibrium-Wrapper + Vaults | collect | Phase 2; Daten bleiben zunächst in `~/collect/data/`, Pfade nur via Config |
| Ein kanonisches Embedding-Backend | neu | Phase 2; die `embed_sync` vs `encode`-Divergenz war Bug-Quelle Nr. 1 |
| Drei-Zonen-Logik, Centroid-Routing | collect | Phase 2 |
| query_translator, query_decomposer, vault_router | vibelike | Phase 2 |
| BaseAgent/Redis-Backbone | collect (Muster) | Phase 3, neu geschrieben nach §5-Regeln |
| Plan→Decide→Act-Logik | collect | Phase 3; Logik ja, Implementierung neu |
| validator2 (Static-Validator vor Writes) | vibelike | verschoben nach Phase 4 (666 LOC Vollport); Phase 3 nutzt Syntax-Check + regression_guard.check_paths als Pre-Write-Gate |
| Ossifikat (Submodule), Idiom-System, Triplet-Logging | vibelike | Phase 4 |
| sandbox, reqqueue, logdb | vibelike | Phase 4/5, nach Bedarf |
| Terminal/REPL, REST-API, Watchdog | beide | Phase 5, klein geschnitten |

**Explizit NICHT übernehmen:** `workflow_agent.py` (3.587 LOC), `terminal.py`
(1.875 LOC), `harvest.py` (1.776 LOC) aus vibelike als Ganzes; die 38
Agent-Dateien aus collect als Ganzes; alle `attic/`/`experiments/`-Zonen.

## 5. Design-Regeln (Lehren aus beiden Audits)

1. **Kein Modul über ~400 LOC, keine Funktion über ~60 LOC.** Die Monolithen
   waren vibelikes größtes benanntes Problem; nicht wiederholen.
2. **Ein Import-Pfad:** alles unter `src/collect/`, importiert als `collect.*`.
   Keine dual-path-Fallbacks, keine `sys.path`-Hacks, keine Root-Module.
3. **Agenten-Logik von Transport trennen:** jeder Agent ist eine reine
   Handler-Funktion/-Klasse (`dict → dict`), die ohne Redis unit-testbar ist.
   Der Redis-Listener ist eine dünne, geteilte Hülle.
4. **Idempotenz per Design:** jede Nachricht trägt eine ID; Handler müssen
   Duplikate tolerieren (die Doppelzählungs-Bugs im Alt-System entstanden, weil
   Idempotenz nachgerüstet wurde).
5. **Finalisierung explizit statt heuristisch:** der Orchestrator sagt dem
   ResponseAgent, *welche* Beiträge kommen werden (Contribution-Manifest);
   finalisiert wird bei Vollständigkeit oder Deadline — nie per Ratelogik.
6. **Ein Embedding-Backend** mit einer API (`embed(texts) -> np.ndarray`),
   überall dieselbe Instanz. Device via Config (`cpu` default — VRAM gehört
   der LLM, siehe RX-7600-Erfahrung).
7. **Daten-Pfade ausschließlich via Config**, keine hartkodierten Homes.
8. **python3.12 gepinnt** (3.14 bricht protobuf/grpc-Gencode im Alt-Stack).
9. **Sicherheitsnetz zuerst:** doctor + regression_guard + CI stehen, bevor
   Fachlogik einzieht. Pre-Commit-Hook blockt Symbol-Verlust.
10. **Jede Phase endet lauffähig und testgrün.**

## 6. Phasenplan

- **Phase 0 — Sichern** ✅ Alt-collect committet (`chore: Stand vor Neuaufbau
  sichern`), Entscheidungen fixiert (dieses Dokument).
- **Phase 1 — Skelett + Sicherheitsnetz** ✅ src-Layout, pyproject, Pydantic-
  Config, doctor, regression_guard, Pre-Commit-Hook, CI, erste Tests.
- **Phase 2 — Retrieval-Kern** ✅ `collect.retrieval`: Vault-Cipher (numpy,
  byte-kompatibel zu Alt-Vaults), ChaosRetrieval/ResonanceField (Port aus
  vibelike), ein Embedding-Backend, Centroid-Routing, Drei-Zonen,
  Translator/Decomposer, RetrievalService mit RRF-Fusion. Integration gegen
  echte Vaults als skipif-Tests.
- **Phase 3 — Redis-Orchestrierung** ✅ Bus (Redis + InMemory), BaseAgent,
  8 Kern-Agenten (orchestrator, retrieval, code_retrieval, llm, decision,
  planning, executor, response), Contribution-Manifest-Finalisierung,
  Plan→Decide→Act, Executor mit Pre-Write-Gate; `collect-agents`/`collect-ask`.
  Channels laufen unter Prefix `c2.` (Kollisionsschutz zum Alt-System).
- **Phase 4 — Grounding & Lernen** ✅ Ossifikat als Submodule; FactGrounder
  (verbürgte Fakten erden Antworten, heben Hard-Fallback auf); LearningAgent
  (Triplet-Log für alle Antworten, Fakt-Extraktion → Staging NUR für
  TRUST-Antworten; verbürgt erst nach menschlicher Bestätigung);
  Security-Scan (validator2-Kern) im Executor-Write-Gate.
  **Bewusst verschoben:** Idiom-System — hat ohne Code-Generierungs-Workflow
  keinen Consumer; Port als toter Scaffold wäre genau der Fehler aus vibelikes
  Audit (§Scaffolds). Kommt mit dem Code-Workflow (Phase 5+).
- **Phase 5 — Oberflächen & Betrieb** ✅ REPL (collect-repl: Fragen, /status,
  /review = menschliche Fakten-Bestätigung, /facts), REST-API (collect-api,
  Port 8767, localhost-only), Status-Schicht (Heartbeats → Redis-Hash),
  Start/Stop/Status-Skripte + systemd-User-Units (Auto-Restart ersetzt den
  Bash-Watchdog), Daten-Migration (SHA256-verifiziert kopiert nach
  ~/collect2/data, COLLECT_DATA_DIR umgestellt).
  **Offen (bewusst):** endgültiger Cutover = Alt-Stack stoppen + Repos
  archivieren — Entscheidung des Users, Alt-System läuft bis dahin parallel
  (Channel-Prefix trennt). API-Härtung (Auth) vor jedem Nicht-localhost-Betrieb.

## 7. Offene Punkte

- Endgültiger Projektname (Arbeitstitel `collect2`, Paketname `collect`).
- GitHub-Remote ja/nein (CI-Workflow liegt bereit).
- Daten-Migration: wann ziehen die Vaults von `~/collect/data/` um (Phase 5),
  und ob Embedding-Caches neu aufgebaut oder kopiert werden.
- Cloud-API-Fallback-Kette (Phase 5+, lokal-first bleibt Default).
