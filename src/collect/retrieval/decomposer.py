"""QueryDecomposer — mehr-aspektige Queries in Teilfragen zerlegen (Port aus vibelike).

Motivation: "Zusammenhang zwischen X und Y" trifft mit EINEM Embedding nur den
dominanten Anker. Zerlegt in Teilfragen + getrennt retrievt + per RRF fusioniert
ist jeder Anker geerdet. Heuristik-Gate: LLM-Call nur bei Mehr-Aspekt-Indikatoren;
Ollama Structured Output (JSON-Schema) erzwingt sauberes Ergebnis; graceful
Fallback auf [original].
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests

from collect.config import settings

logger = logging.getLogger(__name__)

MAX_SUBQUERIES = 3

_MULTI = re.compile(
    r"\b(und|and|sowie|versus|vs|zwischen|between|unterschied|difference|"
    r"compare|vergleich|relationship|beziehung|zusammenhang|verbindung|both|jeweils)\b",
    re.IGNORECASE,
)

_SCHEMA = {
    "type": "object",
    "properties": {"subqueries": {"type": "array", "items": {"type": "string"}}},
    "required": ["subqueries"],
}


def looks_multi_aspect(text: str) -> bool:
    words = re.findall(r"\w+", text)
    return len(words) >= 6 and bool(_MULTI.search(text))


def _cache_key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}|{text.strip().lower()}".encode()).hexdigest()[:16]


class QueryDecomposer:
    """Cached Query-Zerlegung via Ollama (JSON-Schema)."""

    def __init__(self, model: Optional[str] = None, timeout: float = 20.0,
                 cache_file: Optional[Path] = None, enable_cache: bool = True):
        self.model = model or settings.decompose_model
        self.generate_url = f"{settings.ollama_url}/api/generate"
        self.timeout = timeout
        self.enable_cache = enable_cache
        self.cache_file = Path(cache_file) if cache_file else (
            settings.cache_dir / "decompose_cache.json")
        self._cache: dict = self._load_cache() if enable_cache else {}
        self._session = requests.Session()

    def _load_cache(self) -> dict:
        if not self.cache_file.exists():
            return {}
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cache(self) -> None:
        if not self.enable_cache:
            return
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _build_prompt(self, query: str) -> str:
        return (
            "Break the following search query into 2-3 focused, self-contained sub-queries, "
            "ONE per distinct concept or domain it touches. Each sub-query must stand alone "
            "(no pronouns referring to the others) and be in English, suitable for keyword/"
            "semantic search in technical & scientific documents. Do NOT add concepts the "
            "query does not mention. If the query is already single-topic, return it unchanged "
            "as the only element.\n\n"
            f"Query: {query}\n"
            'Respond as JSON: {"subqueries": ["...", "..."]}'
        )

    def _call_ollama(self, prompt: str) -> Optional[dict]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": _SCHEMA,  # Structured Output → valides JSON erzwungen
            "keep_alive": "10m",
            "options": {"num_predict": 200, "temperature": 0.0},
        }
        try:
            r = self._session.post(self.generate_url, json=payload, timeout=self.timeout)
            if r.status_code != 200:
                return None
            return json.loads(r.json().get("response", "") or "{}")
        except Exception:
            return None

    def _clean(self, raw_subs) -> list:
        out, seen = [], set()
        for s in (raw_subs or []):
            if not isinstance(s, str):
                continue
            s = s.strip().strip("\"'").strip()
            key = s.lower()
            if len(s) >= 3 and key not in seen:
                seen.add(key)
                out.append(s)
        return out[:MAX_SUBQUERIES]

    def decompose(self, query: str) -> dict:
        """→ {original, subqueries, skipped, cache_hit, duration_ms}.

        subqueries enthält IMMER mindestens [original]; skipped=True ⇒ kein Fan-out.
        """
        original = query.strip()
        t0 = time.time()
        base = {"original": original, "subqueries": [original], "skipped": True,
                "cache_hit": False, "duration_ms": 0.0}

        if not looks_multi_aspect(original):
            base["duration_ms"] = (time.time() - t0) * 1000
            return base

        cache_id = _cache_key(original, self.model)
        if self.enable_cache and cache_id in self._cache:
            subs = self._cache[cache_id].get("subqueries") or [original]
            return {"original": original, "subqueries": subs,
                    "skipped": len(subs) <= 1, "cache_hit": True,
                    "duration_ms": (time.time() - t0) * 1000}

        data = self._call_ollama(self._build_prompt(original))
        subs = self._clean(data.get("subqueries") if isinstance(data, dict) else None)
        # < 2 brauchbare Teilfragen ⇒ kein Gewinn, beim Original bleiben.
        if len(subs) < 2:
            subs = [original]

        if self.enable_cache:
            self._cache[cache_id] = {"original": original, "subqueries": subs,
                                     "model": self.model,
                                     "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._save_cache()

        return {"original": original, "subqueries": subs,
                "skipped": len(subs) <= 1, "cache_hit": False,
                "duration_ms": (time.time() - t0) * 1000}
