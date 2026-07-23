#!/bin/bash
# Neustart des collect2-Stacks (stop + start).
# Usage:
#   bash scripts/restart.sh          # nur Agenten
#   bash scripts/restart.sh --api    # Stack + REST-API
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "= Stoppe…"
bash "$ROOT/scripts/stop.sh"

sleep 1

echo "= Starte…"
bash "$ROOT/scripts/start.sh" "${@}"

# Vault-Load kann ~45s-3min dauern — kurze Info
if [[ "${1:-}" == "--api" ]]; then
    echo
    echo "= Hart-Reload im Browser: Ctrl+Shift+R (Cache überspringen), dann http://127.0.0.1:8767/chat"
fi
