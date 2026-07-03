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
