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
