#!/bin/bash
# Stoppt Agenten-Stack und (falls gestartet) REST-API.
set -u

stop_one() {
    local name="$1" pidfile="$2"
    if [[ ! -f "$pidfile" ]]; then
        echo "= $name: kein PID-File — läuft nicht (über dieses Skript)."
        return
    fi
    local pid
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        for _ in {1..10}; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.5
        done
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
        echo "✓ $name gestoppt (PID $pid)."
    else
        echo "= $name: Prozess $pid lief nicht mehr."
    fi
    rm -f "$pidfile"
}

stop_one "API"   /tmp/collect2-api.pid
stop_one "Stack" /tmp/collect2-agents.pid
