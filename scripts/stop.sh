#!/bin/bash
# Stoppt den mit scripts/start.sh gestarteten Agenten-Stack.
set -u
PIDFILE=/tmp/collect2-agents.pid
if [[ ! -f "$PIDFILE" ]]; then
    echo "Kein PID-File — nichts zu stoppen."
    exit 0
fi
PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in {1..10}; do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.5
    done
    kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
    echo "Gestoppt (PID $PID)."
else
    echo "Prozess $PID lief nicht mehr."
fi
rm -f "$PIDFILE"
