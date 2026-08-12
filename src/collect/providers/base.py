"""LLMProvider Protocol — einheitliche Schnittstelle für lokale + Cloud-Modelle.

generate_fn-Signatur kompatibel mit ollama.generate / ollama.generate_streaming:
- generate(prompt, system, timeout) -> str
- generate_streaming(prompt, system, on_token, timeout) -> (str, dict)
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol


class LLMProvider(Protocol):
    name: str  # "ollama", "deepseek", "claude", "gemini"

    def generate(self, prompt: str, system: str = "",
                 timeout: float = 120) -> str: ...

    def generate_streaming(self, prompt: str, system: str = "",
                           on_token: Optional[Callable[[str], None]] = None,
                           timeout: float = 120) -> tuple[str, dict]: ...
