#!/bin/bash
# Startet den Agenten-Stack im Hintergrund (ohne systemd; für systemd siehe deploy/).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE=/tmp/collect2-agents.pid
LOG="$ROOT/logs/agents.log"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Läuft bereits (PID $(cat "$PIDFILE"))."
    exit 0
fi
mkdir -p "$ROOT/logs"
cd "$ROOT" || exit 1
( setsid nohup "$ROOT/.venv/bin/collect-agents" > "$LOG" 2>&1 < /dev/null &
  echo $! > "$PIDFILE" )
sleep 2
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Gestartet (PID $(cat "$PIDFILE")). Log: $LOG"
else
    echo "Start fehlgeschlagen — siehe $LOG"
    exit 1
fi
