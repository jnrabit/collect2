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

## Sicherheitsnetz (vor jeder Änderung / im CI)

```bash
collect-doctor            # syntax · config · imports · regression
collect-doctor --fast     # CI-Gate (syntax + config)
collect-guard --staged    # Regression-Guard manuell
pytest
```

## Stand

Phase 1 (Skelett + Sicherheitsnetz) — siehe DESIGN.md §6 für den Phasenplan.
Die Vaults (General 259k / Code 1.7k Docs) liegen bis zur Migration in
`~/collect/data/` und werden via `COLLECT_DATA_DIR` referenziert.
