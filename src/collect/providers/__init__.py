"""Provider — LLM-Provider (lokal + Cloud) mit einheitlichem generate_fn-Interface."""

from collect.providers.base import LLMProvider
from collect.providers.deepseek import DeepSeekProvider

__all__ = ["LLMProvider", "DeepSeekProvider"]
