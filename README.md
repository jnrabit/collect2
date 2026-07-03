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
