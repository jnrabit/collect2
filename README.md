# collect2

Neuaufbau des `collect`-Multi-Agent-Wissenssystems. **Architektur- und
Vorgehens-Anker: [DESIGN.md](DESIGN.md)** — dort stehen die Grundsatz-
Entscheidungen (Redis-Multi-Agent, lokal-first, Phasenplan) und die
Design-Regeln aus den Audits der Vorgänger (`~/collect`, `~/vibelike`).

## Setup

```bash
python3.12 -m venv .venv          # 3.12 gepinnt, siehe DESIGN.md §5.8
.venv/bin/pip install -e ".[dev]"
git config core.hooksPath .githooks   # Pre-Commit-Regression-Guard aktivieren
cp .env.example .env               # optional, Defaults sind lauffähig
```

## Betrieb

```bash
bash scripts/start.sh      # Agenten-Stack (Hintergrund, PID-File)
bash scripts/status.sh     # Heartbeats aller Agenten
bash scripts/stop.sh
collect-repl               # interaktiv: Fragen, /status, /review, /facts
collect-ask "Frage…"       # Einzelanfrage
collect-api                # REST: /api/health, /api/query, /api/facts (Port 8767)
# Dauerbetrieb: deploy/*.service (systemd-User-Units, Anleitung im File)
```

## Harvest (Wissens-Ingest in den Vault)

Der Sammelbefehl für neue Docs. Kein Daemon, kein Scheduler — Vault-Writes
bleiben bewusst ein manueller Auslöser.

```bash
collect-harvest deep                    # DER Sammelbefehl: alle vier Frontier-Quellen
                                        # OpenAlex + Semantic Scholar + StackExchange + Gutenberg
collect-harvest all "quantum chaos"     # Wikipedia + ArXiv + RFC zu einem Thema
collect-harvest explore --lang de       # autonom: Wiki-SURF + Multi-Source-DEEPEN
collect-explore --lang de --deep        # dasselbe direkt, mit S2 + OpenAlex statt nur ArXiv

# Einzelquellen
collect-harvest wikipedia "HTTP protocol" --limit 300 --lang en
collect-harvest arxiv "transformer attention" --limit 10
collect-harvest rfc --limit 5
collect-harvest semantic | openalex | gutenberg | stackexchange
```

| Flag | Wirkung |
|---|---|
| `--limit N` | max. neue Docs (0 = Default: 500 bei `all`, sonst 200) |
| `--lang en\|de` | Sprache für Wikipedia-Quellen |
| `--dry-run` | nur sammeln + prüfen, **nicht** in den Vault schreiben |
| `--no-expand` | keine Wikipedia-Link-Expansion |

Eigenschaften:
- **Idempotent** — bereits vorhandene Docs (per ID) werden nicht erneut gefetcht.
- **Abbrechbar** — Ctrl-C (SIGINT/SIGTERM) speichert das bereits Gesammelte, statt es zu verwerfen.
- **Backup pro Ingest, mit Rotation** — Archiv und Cache werden vor dem Write nach
  `*.bak-<ts>` kopiert (Rollback: zurückkopieren). Nach erfolgreichem Commit bleiben
  die `COLLECT_INGEST_KEEP_BACKUPS` jüngsten stehen (Default 3), ältere werden
  gelöscht. Schlägt der Write fehl, wird **nicht** rotiert — alle Rollback-Punkte
  bleiben erhalten.
- Der laufende Agenten-Stack hält eine RAM-Kopie und sieht neue Docs erst nach
  `bash scripts/restart.sh`.

### Vault-Pflege

```bash
collect-vault stats       # Bestand, Konsistenz Archiv↔Cache, Quellenverteilung
collect-vault diff        # neue Docs + Cache-Lücken
collect-vault rebuild     # fehlende Vektoren embedden
collect-vault prune       # Waisen-Vektoren entfernen (mit Backup)
collect-vault dedupe      # Duplikate reporten (löscht nie)
```

Ingest-Backups räumt der Ingest seit `COLLECT_INGEST_KEEP_BACKUPS` selbst auf.
Falls doch mal von Hand (z. B. nach einem Abbruch) — pro Datei die 2 neuesten
behalten:

```bash
for f in data/monolith_archive.monolith data/monolith_embedding_cache.pkl \
         data/code_archive.monolith data/code_embedding_cache.pkl; do
  ls -t "$f".bak-* 2>/dev/null | tail -n +3 | xargs -r rm --
done
```

## Beobachter (Autonome Hintergrundprozesse)

```bash
collect-observe start       # Autopilot + SilentObserver + OsmosisObserver starten
collect-observe stop        # Alle Beobachter stoppen (in-process)
collect-observe status      # Laufzeit-Status aller Beobachter
collect-observe dream       # DreamCycle: Apoptose + Meta-Kristallisation (explizit)
collect-observe analyze         # silent_observer.jsonl auswerten
collect-observe analyze osmosis # osmosis_log.jsonl auswerten
```

Konfiguration via `.env` (Prefix `COLLECT_OBSERVE_`):

| Variable | Default | Beschreibung |
|---|---|---|
| `COLLECT_OBSERVE_SILENT_INTERVAL` | 60 | Sekunden zwischen Phantom/Void/Drift-Scans |
| `COLLECT_OBSERVE_OSMOSIS_ENABLED` | true | OsmosisObserver aktivieren |
| `COLLECT_OBSERVE_OSMOSIS_INTERVAL` | 30 | Sekunden zwischen Topologie-Scans |
| `COLLECT_OBSERVE_AUTOPILOT_ENABLED` | true | Autopilot aktivieren |
| `COLLECT_OBSERVE_AUTOPILOT_INTERVAL` | 30 | Sekunden zwischen Query-Generierungen |
| `COLLECT_OBSERVE_AUTOPILOT_CYCLES` | 500 | Max Zyklen (0 = endlos) |

Logs liegen unter `data/`:
- `silent_observer.jsonl` — Phantom/Void/Drift-Ereignisse
- `osmosis_log.jsonl` — Osmose/Vakuum/Kristallisation
- `distillation_data.jsonl` — Autopilot-Distillation (Input für DreamCycle)

## Sicherheitsnetz (vor jeder Änderung / im CI)

```bash
collect-doctor            # syntax · config · imports · regression
collect-doctor --fast     # CI-Gate (syntax + config)
collect-guard --staged    # Regression-Guard manuell
pytest
```

## Stand

Phasen 0–5 abgeschlossen — siehe DESIGN.md §6. Die Vaults (General 259k /
Code 1,5k Docs) liegen migriert in `~/collect2/data/` (`COLLECT_DATA_DIR`
in `.env`); das Alt-System in `~/collect` bleibt unangetastet als Referenz.
Grounding-Schleife: TRUST-Antworten erzeugen Staging-Tripel → `/review` im
REPL verbürgt sie → verbürgte Fakten erden künftige Antworten.
