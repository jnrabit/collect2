"""LearningAgent: Triplet-Log immer, Staging nur für TRUST-Antworten."""

import json

import pytest

from collect.agents.learning import LearningAgent
from collect.bus import InMemoryBus, Message
from collect.config import settings


class FakeExtractor:
    def __init__(self):
        self.calls = []

    def extract_and_stage(self, text, store, source=""):
        self.calls.append(text)
        return store and [store.add_staging("S", "p", f"O{len(self.calls)}",
                                            source=source)] or []


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "triplet_log_file", tmp_path / "triplets.jsonl")
    monkeypatch.setattr(settings, "ossifikat_db", tmp_path / "ossifikat.db")
    bus = InMemoryBus(prefix="test.")
    extractor = FakeExtractor()
    agent = LearningAgent(bus, extractor=extractor)
    agent.start()
    return bus, agent, extractor, tmp_path


def _answer(bus, zone="TRUST", plan_id=None, cid="c1"):
    bus.publish("answer_recorded", Message(
        type="answer_recorded",
        data={"query": "Frage?", "text": "Die Antwort.", "zone": zone,
              "best_distance": 40.0, "plan_id": plan_id,
              "context_ids": ["doc-1", "doc-2"]},
        correlation_id=cid))


def test_trust_answer_logs_and_stages(setup):
    bus, agent, extractor, tmp = setup
    _answer(bus, zone="TRUST")
    lines = (tmp / "triplets.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["meta"]["zone"] == "TRUST"
    assert len(extractor.calls) == 1
    # Tripel landet wirklich im Staging (unbestätigt)
    from ossifikat.store import OssifikatStore
    s = OssifikatStore(str(tmp / "ossifikat.db"))
    assert len(s.list_staging()) == 1
    assert s.query() == []  # nichts verbürgt ohne menschliche Bestätigung
    s.close()


def test_gray_zone_logs_but_does_not_stage(setup):
    bus, agent, extractor, tmp = setup
    _answer(bus, zone="GRAUZONE")
    assert (tmp / "triplets.jsonl").exists()
    assert extractor.calls == []  # kein Halluzinations-Substrat ossifizieren


def test_plan_answers_do_not_stage(setup):
    bus, agent, extractor, tmp = setup
    _answer(bus, zone="TRUST", plan_id="plan_x")
    assert extractor.calls == []


def test_extract_disabled_via_settings(setup, monkeypatch):
    bus, agent, extractor, tmp = setup
    monkeypatch.setattr(settings, "extract_facts", False)
    _answer(bus, zone="TRUST")
    assert extractor.calls == []
