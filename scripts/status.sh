#!/bin/bash
# Agenten-Status (Heartbeats aus Redis).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/.venv/bin/python" -c "
from collect.repl import cmd_status
print(cmd_status())"
