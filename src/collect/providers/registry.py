"""Runtime-Provider-Registry — API-Keys per REST/WS setzbar.

Redis-Backed: API und Agenten laufen in getrennten Prozessen;
Keys/Enabled-Status werden via Redis geteilt (Live-Update ohne Neustart).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from collect.config import settings

logger = logging.getLogger(__name__)

# Redis-Key-Names (über Channel-Präfix genamespaced)
_REDIS_KEY_ENABLED = f"{settings.channel_prefix}api:enabled"
_REDIS_KEY_PREFIX = f"{settings.channel_prefix}api:key:"


def _get_redis():
    """Redis-Client (lazy, gecacht auf Modul-Ebene)."""
    global _redis
    if _redis is None:
        import redis
        _redis = redis.Redis(
            host=settings.redis_host, port=settings.redis_port,
            db=settings.redis_db, decode_responses=True)
    return _redis


_redis = None


def set_key(provider: str, key: str) -> None:
    """API-Key für einen Provider setzen (z.B. 'deepseek')."""
    _get_redis().set(_REDIS_KEY_PREFIX + provider, key)
    logger.info("API-Key für %s gesetzt", provider)


def clear_key(provider: str) -> None:
    """API-Key entfernen → Provider antwortet wieder skipped."""
    _get_redis().delete(_REDIS_KEY_PREFIX + provider)
    logger.info("API-Key für %s entfernt", provider)


def get_key(provider: str) -> str:
    return _get_redis().get(_REDIS_KEY_PREFIX + provider) or ""


def set_enabled(enabled: bool) -> None:
    _get_redis().set(_REDIS_KEY_ENABLED, "1" if enabled else "0")


def is_enabled() -> bool:
    return _get_redis().get(_REDIS_KEY_ENABLED) == "1"


def status() -> dict:
    """Snapshot für die Web-UI."""
    r = _get_redis()
    enabled = r.get(_REDIS_KEY_ENABLED) == "1"
    # Alle Keys mit dem Präfix scannen (kein KEY * — safe auf kleinen Instanzen)
    providers = {}
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match=_REDIS_KEY_PREFIX + "*", count=20)
        for k in keys:
            name = k[len(_REDIS_KEY_PREFIX):]
            providers[name] = bool(r.get(k))
        if cursor == 0:
            break
    return {"enabled": enabled, "providers": providers}


class LazyDeepSeekProvider:
    """DeepSeek-Wrapper, der den Key aus Redis bezieht.

    Ohne Key: generate/generate_streaming gibt sofort ("", {skipped:True}) zurück.
    Mit Key: delegiert an echten DeepSeekProvider.
    """
    name = "deepseek"

    def __init__(self, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com/v1",
                 timeout: float = 120):
        self.model = model
        self._base_url = base_url
        self._timeout = timeout
        self._real: Optional[object] = None

    def _ensure(self) -> Optional[object]:
        key = get_key("deepseek")
        if not key:
            self._real = None
            return None
        if self._real is not None and self._real._api_key != key:
            self._real = None
        if self._real is None:
            from collect.providers.deepseek import DeepSeekProvider
            self._real = DeepSeekProvider(
                api_key=key, model=self.model,
                base_url=self._base_url, timeout=self._timeout)
        return self._real

    def generate(self, prompt: str, system: str = "",
                 timeout: Optional[float] = None) -> str:
        if not is_enabled():
            return ""
        real = self._ensure()
        if real is None:
            return ""
        return real.generate(prompt, system=system, timeout=timeout)

    def generate_streaming(self, prompt: str, system: str = "",
                           on_token: Optional[Callable[[str], None]] = None,
                           timeout: Optional[float] = None) -> tuple[str, dict]:
        if not is_enabled():
            return "", {"skipped": True, "model": self.model}
        real = self._ensure()
        if real is None:
            return "", {"skipped": True, "model": self.model}
        return real.generate_streaming(prompt, system=system,
                                       on_token=on_token, timeout=timeout)
