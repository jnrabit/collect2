"""Agenten-Status — Heartbeats aus Redis lesen (für REPL, API, Healthchecks).

Der Runner schreibt pro Heartbeat zusätzlich in den Redis-Hash
`{prefix}agents:status` (agent → unix-ts). Pub/Sub allein wäre ephemer;
der Hash macht den Zustand abfragbar, ohne mitgehört haben zu müssen.
"""

from __future__ import annotations

import time

from collect.config import settings

STALE_AFTER = 3 * 10.0  # 3 verpasste Heartbeat-Intervalle → stale


def status_key() -> str:
    return f"{settings.channel_prefix}agents:status"


def write_heartbeat(redis_client, agent_name: str) -> None:
    try:
        redis_client.hset(status_key(), agent_name, time.time())
    except Exception:
        pass  # Status ist best-effort


def get_agent_status(redis_client=None) -> dict:
    """→ {agent: {"age_s": float, "alive": bool}}; {} wenn Redis weg."""
    if redis_client is None:
        import redis
        redis_client = redis.Redis(
            host=settings.redis_host, port=settings.redis_port,
            db=settings.redis_db, decode_responses=True)
    try:
        raw = redis_client.hgetall(status_key())
    except Exception:
        return {}
    now = time.time()
    stale = max(STALE_AFTER, settings.heartbeat_interval * 3)
    return {
        name: {"age_s": round(now - float(ts), 1),
               "alive": (now - float(ts)) < stale}
        for name, ts in raw.items()
    }
