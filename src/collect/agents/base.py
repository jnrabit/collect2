"""BaseAgent — gemeinsame Hülle: Subscriptions, Idempotenz, Progress.

Bewusst klein: kein eigener Thread (das macht der Bus), kein Blocking-Wait
(Handler sind Zustandsmaschinen). Duplikate werden über message_id verworfen
(DESIGN.md §5.4 — Idempotenz per Design, nicht nachgerüstet).
"""

from __future__ import annotations

import logging
from collections import OrderedDict

from collect.bus import BaseBus, Message

MAX_SEEN = 2048


class BaseAgent:
    name = "base"

    def __init__(self, bus: BaseBus):
        self.bus = bus
        self.log = logging.getLogger(f"agent.{self.name}")
        self._seen: OrderedDict[str, None] = OrderedDict()

    # ── Verdrahtung ──────────────────────────────────────────────────────

    def subscriptions(self) -> dict:
        """channel → handler(Message). Von Subklassen überschrieben."""
        return {}

    def start(self) -> None:
        self.bus.register(self)

    # ── Zustellung ───────────────────────────────────────────────────────

    def handle(self, channel: str, message: Message) -> None:
        if message.message_id in self._seen:
            self.log.debug("Duplikat verworfen: %s", message.message_id)
            return
        self._seen[message.message_id] = None
        if len(self._seen) > MAX_SEEN:
            self._seen.popitem(last=False)
        handler = self.subscriptions().get(channel)
        if handler:
            handler(message)

    # ── Helfer ───────────────────────────────────────────────────────────

    def publish(self, channel: str, type: str, data: dict,
                correlation_id: str, reply_to: str = "") -> None:
        self.bus.publish(channel, Message(
            type=type, data=data, correlation_id=correlation_id,
            reply_to=reply_to, source=self.name))

    def progress(self, correlation_id: str, stage: str, detail: str = "") -> None:
        """Best-effort Lifecycle-Event fürs Terminal — darf nie etwas brechen."""
        try:
            self.publish("progress", "progress",
                         {"stage": stage, "detail": detail}, correlation_id)
        except Exception:
            pass
