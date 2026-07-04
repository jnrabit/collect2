"""Synchroner Ollama-Client für Agenten-Handler.

Bewusst blockierend mit hartem Timeout statt async: der async-Pfad
(`run_until_complete` aus Listener-Threads) hing im Alt-System stundenlang.
Jeder Agent läuft in seinem eigenen Bus-Thread — Blockieren ist dort ok.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from collect.config import settings

logger = logging.getLogger(__name__)


def generate(prompt: str, system: str = "", model: Optional[str] = None,
             timeout: Optional[float] = None, temperature: float = 0.1,
             fmt: Optional[dict] = None) -> str:
    """Nicht-streamender Generate-Call. Wirft bei HTTP-/Timeout-Fehlern."""
    payload = {
        "model": model or settings.main_model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system
    if fmt:
        payload["format"] = fmt
    resp = requests.post(
        f"{settings.ollama_url}/api/generate",
        json=payload,
        timeout=timeout or settings.llm_timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def generate_streaming(prompt: str, system: str = "", model: Optional[str] = None,
                       timeout: Optional[float] = None, temperature: float = 0.1,
                       on_token=None) -> tuple[str, dict]:
    """Streamender Generate-Call: on_token(delta) pro Chunk, → (text, stats).

    stats aus Ollamas Final-Chunk: eval_count (generierte Tokens) und
    tok_per_s (eval_count/eval_duration). Der timeout gilt pro Chunk-Read —
    ein aktiv streamendes Modell läuft also nie in den Timeout.
    """
    import json as _json

    payload = {
        "model": model or settings.main_model,
        "prompt": prompt,
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system

    parts: list[str] = []
    stats: dict = {}
    with requests.post(f"{settings.ollama_url}/api/generate", json=payload,
                       stream=True, timeout=timeout or settings.llm_timeout) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                chunk = _json.loads(line)
            except ValueError:
                continue
            delta = chunk.get("response", "")
            if delta:
                parts.append(delta)
                if on_token is not None:
                    on_token(delta)
            if chunk.get("done"):
                ec = chunk.get("eval_count")
                ed = chunk.get("eval_duration")
                stats = {
                    "eval_count": ec,
                    "tok_per_s": round(ec / (ed / 1e9), 1) if ec and ed else None,
                }
    return "".join(parts), stats
