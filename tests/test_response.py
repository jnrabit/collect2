"""ResponseAgent: Manifest-Finalisierung (Vollständigkeit/Deadline) + Synthese."""

from collect.agents.response import ResponseAgent, synthesize
from collect.bus import InMemoryBus, Message


def _bus_with_agent():
    bus = InMemoryBus(prefix="test.")
    agent = ResponseAgent(bus)
    agent.start()
    return bus, agent


def _manifest(bus, cid, expected, reply_to="user_response.x"):
    bus.publish("response_manifest", Message(
        type="response_manifest",
        data={"query": "q", "expected": expected, "deadline": 60},
        correlation_id=cid, reply_to=reply_to))


def _contrib(bus, cid, channel, data):
    bus.publish(channel, Message(type=channel, data=data, correlation_id=cid))


def _responses(bus, reply_to="user_response.x"):
    return [m for ch, m in bus.published if ch == reply_to]


def test_finalizes_when_manifest_complete():
    bus, _ = _bus_with_agent()
    _manifest(bus, "c1", ["retrieval", "llm"])
    _contrib(bus, "c1", "retrieval_response",
             {"hits": [], "zone": "TRUST", "best_distance": 40.0, "count": 3})
    assert _responses(bus) == []  # noch unvollständig
    _contrib(bus, "c1", "llm_response", {"content": "Die Antwort.", "skipped": False})
    out = _responses(bus)
    assert len(out) == 1
    assert "Die Antwort." in out[0].data["text"]
    assert out[0].data["meta"]["finalize_reason"] == "complete"
    assert out[0].data["meta"]["zone"] == "TRUST"


def test_finalizes_once_only():
    bus, _ = _bus_with_agent()
    _manifest(bus, "c1", ["llm"])
    _contrib(bus, "c1", "llm_response", {"content": "A"})
    _contrib(bus, "c1", "llm_response", {"content": "B"})  # Nachzügler
    assert len(_responses(bus)) == 1


def test_deadline_finalizes_partial():
    bus, _ = _bus_with_agent()
    _manifest(bus, "c1", ["retrieval", "llm"])
    _contrib(bus, "c1", "retrieval_response",
             {"hits": [], "zone": "TRUST", "best_distance": 30.0, "count": 2})
    bus.run_due(now=float("inf"))  # Deadline feuert
    out = _responses(bus)
    assert len(out) == 1
    assert out[0].data["meta"]["finalize_reason"] == "deadline"
    assert "Ausstehend geblieben: llm" in out[0].data["text"]


def test_unknown_correlation_ignored():
    bus, _ = _bus_with_agent()
    _contrib(bus, "nie-gesehen", "llm_response", {"content": "x"})
    assert _responses(bus) == []


# ── synthesize (rein) ────────────────────────────────────────────────────

def _state(expected, contribs):
    return {"query": "q", "expected": set(expected), "contribs": contribs,
            "reply_to": "r", "finalized": False, "started_at": 0}


def test_synthesize_gray_zone_hint():
    text, meta = synthesize(_state(["retrieval", "llm"], {
        "retrieval": {"zone": "GRAUZONE", "best_distance": 59.0, "count": 2, "hits": []},
        "llm": {"content": "Antwort mit Vorsicht."},
    }))
    assert meta["zone"] == "GRAUZONE"
    assert "entfernte Vault-Treffer" in text
    assert "Antwort mit Vorsicht." in text


def test_synthesize_fallback_suppresses_llm():
    text, meta = synthesize(_state(["retrieval", "llm"], {
        "retrieval": {"zone": "FALLBACK", "best_distance": 75.0, "count": 1, "hits": []},
        "llm": {"content": "Halluzinierte Antwort."},
    }))
    assert meta["zone"] == "FALLBACK"
    assert "Halluzinierte Antwort." not in text
    assert "außerhalb des indizierten Wissensbereichs" in text


def test_synthesize_plan_first_and_never_suppressed():
    text, meta = synthesize(_state(["planning"], {
        "planning": {"message": "Ausführungsergebnis für: x\n\n✓ Schritt 1",
                     "plan_id": "plan_1", "executed": True},
    }))
    assert text.startswith("Ausführungsergebnis")
    assert meta["plan_id"] == "plan_1"
    # FALLBACK-Zone (keine Retrieval-Beiträge) unterdrückt Plan NICHT
    assert meta["zone"] == "FALLBACK"


def test_synthesize_best_distance_over_both_vaults():
    _, meta = synthesize(_state(["retrieval", "code_retrieval", "llm"], {
        "retrieval": {"zone": "FALLBACK", "best_distance": 80.0, "count": 1, "hits": []},
        "code_retrieval": {"zone": "TRUST", "best_distance": 45.0, "count": 2, "hits": []},
        "llm": {"content": "ok"},
    }))
    assert meta["best_distance"] == 45.0
    assert meta["zone"] == "TRUST"
