"""Qwythos-Serving — Tool-Executor + modellgetriebener Agent (K4N0N3-Auftrag).

Das Trace-/Trainingsdaten-Modul liegt getrennt unter collect.traces.
"""

from collect.qwythos.tools import (
    TOOL_DEFINITIONS, SYSTEM_PROMPT, call_tool, compute_schema_hash,
)
