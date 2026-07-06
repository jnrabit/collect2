"""QueryTranslator — Pre-Retrieval-Hook DE→EN (Port aus vibelike).

Der General-Vault ist EN-lastig (Wikipedia/RFC/PEP/ARXIV); deutsche Queries
treffen ohne Übersetzung schlecht. Cheap-Check-Heuristik vermeidet LLM-Calls
für englische Queries, JSON-Cache vermeidet Wiederholungs-Übersetzungen,
bei LLM-Ausfall Fallback aufs Original.
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

_GERMAN_CHARS = set("äöüÄÖÜß")
_GERMAN_STOPWORDS = {
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einen", "einem", "einer", "eines",
    "und", "oder", "aber", "wenn", "weil", "dass",
    "ich", "du", "er", "sie", "es", "wir", "ihr",
    "nicht", "mit", "von", "zu", "auf", "in", "aus",
    "ist", "war", "sind", "wird", "wurde", "werden",
    "wie", "was", "wo", "wann", "warum", "welche",
    "fuer", "auch", "noch", "schon", "nur",
}


def looks_german(text: str) -> bool:
    if any(c in _GERMAN_CHARS for c in text):
        return True
    words = re.findall(r"[a-zA-ZäöüÄÖÜß]+", text.lower())
    return sum(1 for w in words if w in _GERMAN_STOPWORDS) >= 1


def _cache_key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}|{text.strip().lower()}".encode()).hexdigest()[:16]


class QueryTranslator:
    """Cached DE→EN-Übersetzung via Ollama."""

    def __init__(self, model: Optional[str] = None, timeout: float = 15.0,
                 cache_file: Optional[Path] = None, enable_cache: bool = True):
        # Leerer translate_model → Generalist (main_model); kleine Modelle
        # übersetzen Fachbegriffe falsch und korrumpieren das Retrieval.
        self.model = model or settings.translate_model or settings.main_model
        self.generate_url = f"{settings.ollama_url}/api/generate"
        self.timeout = timeout
        self.enable_cache = enable_cache
        self.cache_file = Path(cache_file) if cache_file else (
            settings.cache_dir / "translation_cache.json")
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
            pass  # Cache ist best-effort

    def _call_ollama(self, prompt: str) -> Optional[str]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",
            "options": {"num_predict": 60, "temperature": 0.0, "top_p": 0.9},
        }
        try:
            r = self._session.post(self.generate_url, json=payload, timeout=self.timeout)
            if r.status_code != 200:
                return None
            return r.json().get("response", "").strip()
        except Exception:
            return None

    def _build_prompt(self, query: str) -> str:
        return (
            "Translate the following German query into concise English suitable "
            "for keyword search in technical/scientific documents. "
            "Keep technical terms (TLS, HTTP, RAM, etc.) unchanged. "
            "Output ONLY the translated query — no quotes, no explanation, no prefix.\n\n"
            f"German: {query}\nEnglish:"
        )

    def _clean_response(self, raw: str) -> str:
        s = raw.strip().strip("\"'")
        if ":" in s and len(s.split(":", 1)[0]) <= 12:
            s = s.split(":", 1)[1].strip()
        # Nach Prefix-Drop können erneut Quotes außen stehen ('English: "..."')
        return s.split("\n")[0].strip().strip("\"'")

    def translate(self, query: str) -> dict:
        """→ {original, translated, lang_detected, skipped, cache_hit, duration_ms}"""
        original = query.strip()
        t0 = time.time()

        if len(original) < 3 or not looks_german(original):
            return {"original": original, "translated": original,
                    "lang_detected": "en", "skipped": True,
                    "cache_hit": False, "duration_ms": (time.time() - t0) * 1000}

        cache_id = _cache_key(original, self.model)
        if self.enable_cache and cache_id in self._cache:
            return {"original": original,
                    "translated": self._cache[cache_id]["translated"],
                    "lang_detected": "de", "skipped": False,
                    "cache_hit": True, "duration_ms": (time.time() - t0) * 1000}

        raw = self._call_ollama(self._build_prompt(original))
        if not raw:
            return {"original": original, "translated": original,
                    "lang_detected": "de", "skipped": False,
                    "cache_hit": False, "duration_ms": (time.time() - t0) * 1000}

        translated = self._clean_response(raw) or original
        if self.enable_cache:
            self._cache[cache_id] = {
                "original": original, "translated": translated,
                "model": self.model,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._save_cache()

        return {"original": original, "translated": translated,
                "lang_detected": "de", "skipped": False,
                "cache_hit": False, "duration_ms": (time.time() - t0) * 1000}
