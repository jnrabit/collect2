"""DeepSeekProvider — OpenAI-kompatible Chat-Completions-API.

Streaming via SSE (data: {...}\n\n), non-streaming per /v1/chat/completions.
Kein externes openai-Paket nötig — raw requests wie der Ollama-Client.
"""

from __future__ import annotations

import json as _json
import logging
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, api_key: str, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com/v1",
                 timeout: float = 120):
        self._api_key = api_key
        self.model = model
        base_url = base_url.rstrip("/")
        if not base_url.lower().startswith("https://"):
            raise ValueError(
                "DeepSeek-Base-URL muss HTTPS sein — sonst ginge der API-Key "
                f"im Klartext raus. Erhalten: {base_url!r}")
        self._base_url = base_url
        self._timeout = timeout

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def generate(self, prompt: str, system: str = "",
                 timeout: Optional[float] = None) -> str:
        """Nicht-streamend — für DecisionAgent / Planungs-Calls."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
            "max_tokens": 2048,
        }

        resp = requests.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=self._headers,
            timeout=timeout or self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def generate_streaming(self, prompt: str, system: str = "",
                           on_token: Optional[Callable[[str], None]] = None,
                           timeout: Optional[float] = None) -> tuple[str, dict]:
        """Streamend via SSE — für LLMAgent (interim token-weitergabe)."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": True,
            "max_tokens": 2048,
        }

        parts: list[str] = []
        tok_count = 0
        t0 = time.monotonic()

        with requests.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=self._headers,
            stream=True,
            timeout=timeout or self._timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                line_s = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line_s.startswith("data: "):
                    continue
                data = line_s[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = _json.loads(data)
                except ValueError:
                    continue
                delta = (chunk.get("choices", [{}])[0]
                         .get("delta", {}).get("content", ""))
                if delta:
                    parts.append(delta)
                    tok_count += 1
                    if on_token is not None:
                        try:
                            on_token(delta)
                        except Exception:
                            logger.warning("on_token-Callback fehlgeschlagen",
                                           exc_info=True)

        elapsed = time.monotonic() - t0
        stats = {
            "eval_count": tok_count,
            "tok_per_s": round(tok_count / elapsed, 1) if elapsed > 0 else None,
            "model": self.model,
        }
        return "".join(parts).strip(), stats
