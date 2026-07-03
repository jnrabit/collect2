#!/bin/bash
# Startet den collect2-Agenten-Stack im Hintergrund.
#
# Usage:
#   bash scripts/start.sh          # nur Agenten-Stack
#   bash scripts/start.sh --api    # Stack + REST-API (Port 8767)
#
# Voraussetzungen (werden geprüft): .venv installiert, Redis, Ollama.
# Konfiguration: ~/collect2/.env (siehe .env.example).
# Für Dauerbetrieb mit Auto-Restart: deploy/*.service (systemd, Anleitung im File).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE=/tmp/collect2-agents.pid
API_PIDFILE=/tmp/collect2-api.pid
LOG="$ROOT/logs/agents.log"
API_LOG="$ROOT/logs/api.log"
WITH_API=false
[[ "${1:-}" == "--api" ]] && WITH_API=true

# ── Preflight ────────────────────────────────────────────────────────────
if [[ ! -x "$ROOT/.venv/bin/collect-agents" ]]; then
    echo "✗ .venv fehlt oder unvollständig. Setup:"
    echo "    python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev,retrieval,api]'"
    exit 1
fi
if ! redis-cli ping > /dev/null 2>&1; then
    echo "✗ Redis antwortet nicht (redis-cli ping). Start: systemctl start redis"
    exit 1
fi
if ! curl -sf --max-time 3 localhost:11434/api/tags > /dev/null; then
    echo "✗ Ollama nicht erreichbar (localhost:11434). Start: systemctl start ollama"
    exit 1
fi

# ── Stack ────────────────────────────────────────────────────────────────
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "= Stack läuft bereits (PID $(cat "$PIDFILE"))."
else
    mkdir -p "$ROOT/logs"
    cd "$ROOT" || exit 1
    ( setsid nohup "$ROOT/.venv/bin/collect-agents" > "$LOG" 2>&1 < /dev/null &
      echo $! > "$PIDFILE" )
    # Vault-Load braucht ~45s (259k Docs + Embedding-Modell) — auf "online" warten
    echo -n "Stack startet (Vaults laden, ~45s) "
    for _ in {1..60}; do
        grep -q "Agenten online" "$LOG" 2>/dev/null && break
        kill -0 "$(cat "$PIDFILE")" 2>/dev/null || { echo; echo "✗ Abgestürzt — siehe $LOG"; exit 1; }
        echo -n "."; sleep 2
    done
    echo
    if grep -q "Agenten online" "$LOG" 2>/dev/null; then
        echo "✓ $(grep -o '[0-9]* Agenten online' "$LOG" | tail -1) (PID $(cat "$PIDFILE")). Log: $LOG"
    else
        echo "⚠ Noch nicht bestätigt online — prüfe: bash scripts/status.sh bzw. $LOG"
    fi
fi

# ── API (optional) ───────────────────────────────────────────────────────
if $WITH_API; then
    if [[ -f "$API_PIDFILE" ]] && kill -0 "$(cat "$API_PIDFILE")" 2>/dev/null; then
        echo "= API läuft bereits (PID $(cat "$API_PIDFILE"))."
    else
        ( setsid nohup "$ROOT/.venv/bin/collect-api" > "$API_LOG" 2>&1 < /dev/null &
          echo $! > "$API_PIDFILE" )
        sleep 3
        if curl -sf --max-time 3 localhost:8767/api/health > /dev/null; then
            echo "✓ API auf http://127.0.0.1:8767 (PID $(cat "$API_PIDFILE")). Log: $API_LOG"
        else
            echo "⚠ API antwortet (noch) nicht — siehe $API_LOG"
        fi
    fi
fi
