"""Nachrichten-Bus — dünne Transportschicht über Redis Pub/Sub.

Zwei Implementierungen mit identischem Interface:
  - RedisBus:    Produktion; ein Listener-Thread pro Agent, Timer via threading.
  - InMemoryBus: Tests; synchrone Zustellung, Timer manuell auslösbar
                 (`run_due`) — der gesamte Agenten-Fluss läuft deterministisch
                 ohne Redis (DESIGN.md §5.3).

Alle Channels werden mit settings.channel_prefix versehen (Namespace-Trennung
vom Alt-System, das auf demselben Redis läuft).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from collect.config import settings

logger = logging.getLogger(__name__)


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Message:
    """Einheitlicher Umschlag. message_id macht Handler idempotent (§5.4)."""

    type: str
    data: dict = field(default_factory=dict)
    correlation_id: str = field(default_factory=new_id)
    message_id: str = field(default_factory=new_id)
    reply_to: str = ""
    source: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Message":
        obj = json.loads(raw)
        known = {k: obj[k] for k in cls.__dataclass_fields__ if k in obj}
        return cls(**known)


class BaseBus:
    def __init__(self, prefix: str | None = None):
        self.prefix = settings.channel_prefix if prefix is None else prefix

    def full(self, channel: str) -> str:
        return f"{self.prefix}{channel}"

    def publish(self, channel: str, message: Message) -> None:
        raise NotImplementedError

    def register(self, agent) -> None:
        """Verdrahtet agent.subscriptions(); Zustellung ruft agent.handle()."""
        raise NotImplementedError

    def call_later(self, delay: float, fn) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass


class InMemoryBus(BaseBus):
    """Synchroner Bus für Tests. publish() stellt sofort im selben Thread zu;
    call_later sammelt Timer, die der Test via run_due() auslöst."""

    def __init__(self, prefix: str | None = None):
        super().__init__(prefix)
        self._handlers: dict[str, list] = {}
        self._timers: list[tuple[float, object]] = []  # (due_at, fn)
        self.published: list[tuple[str, Message]] = []  # Protokoll für Asserts

    def subscribe(self, channel: str, handler) -> None:
        self._handlers.setdefault(self.full(channel), []).append(handler)

    def register(self, agent) -> None:
        for channel, handler in agent.subscriptions().items():
            self.subscribe(channel, lambda msg, a=agent, c=channel: a.handle(c, msg))

    def publish(self, channel: str, message: Message) -> None:
        self.published.append((channel, message))
        for handler in list(self._handlers.get(self.full(channel), [])):
            handler(message)

    def call_later(self, delay: float, fn) -> None:
        self._timers.append((time.time() + delay, fn))

    def run_due(self, now: float | None = None) -> int:
        """Löst alle fälligen Timer aus; now=inf löst alles aus. → Anzahl."""
        now = time.time() if now is None else now
        due = [fn for at, fn in self._timers if at <= now]
        self._timers = [(at, fn) for at, fn in self._timers if at > now]
        for fn in due:
            fn()
        return len(due)


class RedisBus(BaseBus):
    def __init__(self, redis_client=None, prefix: str | None = None):
        super().__init__(prefix)
        if redis_client is None:
            import redis
            redis_client = redis.Redis(
                host=settings.redis_host, port=settings.redis_port,
                db=settings.redis_db, decode_responses=True)
        self.redis = redis_client
        self._threads: list[threading.Thread] = []
        self._pubsubs: list = []
        self._timers: list[threading.Timer] = []
        self._running = True

    def publish(self, channel: str, message: Message) -> None:
        self.redis.publish(self.full(channel), message.to_json())

    def register(self, agent) -> None:
        subs = agent.subscriptions()
        if not subs:
            return
        pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
        channel_map = {self.full(c): (c, h) for c, h in subs.items()}
        pubsub.subscribe(*channel_map.keys())
        self._pubsubs.append(pubsub)

        def loop():
            for raw in pubsub.listen():
                if not self._running:
                    break
                try:
                    channel = raw["channel"]
                    short, _handler = channel_map[channel]
                    agent.handle(short, Message.from_json(raw["data"]))
                except Exception:
                    # Ein kaputtes Einzel-Event darf den Listener nie beenden.
                    logger.exception("[%s] Handler-Fehler", agent.name)

        t = threading.Thread(target=loop, name=f"bus-{agent.name}", daemon=True)
        t.start()
        self._threads.append(t)

    def call_later(self, delay: float, fn) -> None:
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        timer.start()
        self._timers.append(timer)

    def stop(self) -> None:
        self._running = False
        for timer in self._timers:
            timer.cancel()
        for ps in self._pubsubs:
            try:
                ps.unsubscribe()
                ps.close()
            except Exception:
                pass
