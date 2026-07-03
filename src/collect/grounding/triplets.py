"""Triplet-Logging — (Query, Kontext, Antwort) reproduzierbar protokollieren.

Aus vibelike übernommenes Prinzip: Pythons hash() ist nicht deterministisch
über Neustarts; SHA256-basierte IDs + JSONL machen Läufe reproduzierbar
debugbar und liefern später Distillations-/Trainingsdaten.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

from collect.config import settings

logger = logging.getLogger(__name__)


def stable_hash(*parts: str) -> str:
    """Deterministische ID über beliebige Text-Teile (restart-stabil)."""
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def log_triplet(query: str, response: str, context_ids: list,
                meta: Optional[dict] = None,
                path: Optional[Path] = None) -> Optional[str]:
    """Hängt ein Triplet ans JSONL-Log an. Best-effort: Fehler werden geloggt,
    nie propagiert. → Triplet-ID oder None."""
    path = Path(path or settings.triplet_log_file)
    entry = {
        "id": stable_hash(query, response),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "query": query,
        "response": response,
        "context_ids": list(context_ids or []),
        "meta": meta or {},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry["id"]
    except OSError as e:
        logger.warning("Triplet-Log fehlgeschlagen: %s", e)
        return None
