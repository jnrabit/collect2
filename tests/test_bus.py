"""Bus + BaseAgent: Zustellung, Idempotenz, Timer."""

from collect.agents.base import BaseAgent
from collect.bus import InMemoryBus, Message


class EchoAgent(BaseAgent):
    name = "echo"

    def __init__(self, bus):
        super().__init__(bus)
        self.received = []

    def subscriptions(self):
        return {"ping": self.on_ping}

    def on_ping(self, msg: Message):
        self.received.append(msg)


def test_message_json_roundtrip():
    m = Message(type="t", data={"a": 1}, correlation_id="c", reply_to="r")
    m2 = Message.from_json(m.to_json())
    assert m2.type == "t" and m2.data == {"a": 1}
    assert m2.message_id == m.message_id


def test_delivery_and_prefix():
    bus = InMemoryBus(prefix="test.")
    agent = EchoAgent(bus)
    agent.start()
    bus.publish("ping", Message(type="ping", data={"x": 1}))
    assert len(agent.received) == 1
    assert bus.published[0][0] == "ping"
    assert bus.full("ping") == "test.ping"


def test_duplicate_message_ignored():
    bus = InMemoryBus(prefix="test.")
    agent = EchoAgent(bus)
    agent.start()
    msg = Message(type="ping", data={})
    bus.publish("ping", msg)
    bus.publish("ping", msg)  # identische message_id
    assert len(agent.received) == 1


def test_unsubscribed_channel_ignored():
    bus = InMemoryBus(prefix="test.")
    agent = EchoAgent(bus)
    agent.start()
    bus.publish("other", Message(type="other", data={}))
    assert agent.received == []


def test_timers_run_due():
    bus = InMemoryBus(prefix="test.")
    fired = []
    bus.call_later(0.0, lambda: fired.append("now"))
    bus.call_later(9999.0, lambda: fired.append("later"))
    assert bus.run_due() == 1
    assert fired == ["now"]
    assert bus.run_due(now=float("inf")) == 1
    assert fired == ["now", "later"]
