"""collect.traces — Trainingsdaten-Pipeline für den WorkflowAgent-Finetune.

Schema (schema.py), Collector (collector.py), Validator (validate.py),
Kuration/Augmentierung (curate.py). CPU-only, keine Vault-Writes.
"""

from collect.traces.schema import (
    TOOL_DEFINITIONS, TraceEntry, TraceMeta, compute_schema_hash, validate_entry,
)
from collect.traces.collector import TraceCollector, get_collector, record_if_enabled
