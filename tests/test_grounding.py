"""Grounding: FactGrounder (echter Ossifikat-Store), Triplets, Security-Scan."""

import json

import pytest

from collect.config import settings
from collect.grounding.facts import FactGrounder
from collect.grounding.triplets import log_triplet, stable_hash
from collect.validation import scan_security, write_blockers
from tests.conftest import fake_embedding


# ── FactGrounder ─────────────────────────────────────────────────────────

@pytest.fixture
def store_db(tmp_path):
    """Echter OssifikatStore: 1 verbürgter + 1 Staging- + 1 retracteter Fakt."""
    from ossifikat.store import OssifikatStore
    db = tmp_path / "ossifikat.db"
    s = OssifikatStore(str(db))
    confirmed = s.add_staging("Python", "ist", "dynamisch typisiert", source="test")
    s.confirm(confirmed, confirmed_by="test")
    s.add_staging("Nur", "im", "Staging", source="test")  # NICHT bestätigt
    retracted = s.add_staging("Alt", "war", "falsch", source="test")
    s.confirm(retracted, confirmed_by="test")
    retract_id = s.retract(s._resolve_id_to_hash(retracted), reason="test")
    if retract_id is not None:  # Retract-Meta-Tripel muss selbst bestätigt werden
        s.confirm(retract_id, confirmed_by="test")
    s.close()
    return db


def test_only_confirmed_facts_ground(store_db):
    g = FactGrounder(fake_embedding, db_path=store_db)
    facts = g.relevant_facts("Python ist dynamisch typisiert", max_dist=2.0)
    contents = [f["content"] for f in facts]
    assert any("dynamisch typisiert" in c for c in contents)
    assert not any("Staging" in c for c in contents)     # unbestätigt → nein
    assert not any("falsch" in c for c in contents)      # retracted → nein
    assert facts[0]["vault"] == "verbürgt"


def test_distance_gate_filters_far_facts(store_db):
    g = FactGrounder(fake_embedding, db_path=store_db)
    # fake_embedding: anderer Text → praktisch orthogonal → dist ~1 > 0.55
    assert g.relevant_facts("voellig anderes thema xyz") == []


def test_missing_db_returns_empty(tmp_path):
    g = FactGrounder(fake_embedding, db_path=tmp_path / "fehlt.db")
    assert g.relevant_facts("egal") == []


def test_disabled_via_settings(store_db, monkeypatch):
    monkeypatch.setattr(settings, "ground_on_facts", False)
    g = FactGrounder(fake_embedding, db_path=store_db)
    assert g.relevant_facts("Python ist dynamisch typisiert") == []


# ── Triplets ─────────────────────────────────────────────────────────────

def test_stable_hash_is_deterministic():
    assert stable_hash("a", "b") == stable_hash("a", "b")
    assert stable_hash("a", "b") != stable_hash("a", "c")
    assert stable_hash("ab") != stable_hash("a", "b")  # Separator wirkt


def test_log_triplet_appends_jsonl(tmp_path):
    p = tmp_path / "triplets.jsonl"
    tid = log_triplet("Frage?", "Antwort.", ["doc-1"], {"zone": "TRUST"}, path=p)
    tid2 = log_triplet("Frage2?", "Antwort2.", [], {}, path=p)
    assert tid and tid2 and tid != tid2
    lines = [json.loads(line) for line in p.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["query"] == "Frage?"
    assert lines[0]["context_ids"] == ["doc-1"]
    assert lines[0]["meta"]["zone"] == "TRUST"


def test_log_triplet_bad_path_is_graceful():
    assert log_triplet("q", "r", [], path="/proc/kein/schreibzugriff.jsonl") is None


# ── Security-Scan (validator2-Kern) ──────────────────────────────────────

def test_scan_finds_eval_and_credential():
    code = 'x = eval(user_input)\npassword = "supergeheim123"\n'
    findings = scan_security(code, "m.py")
    checks = {f["check"] for f in findings}
    assert {"eval_rce", "hardcoded_cred"} <= checks
    assert all(f["severity"] == "high" for f in findings)


def test_write_blockers_only_high():
    code = 'import subprocess\nsubprocess.run(cmd, shell=True)\n'  # medium
    assert write_blockers(code) == []
    assert write_blockers("exec(payload)") != []


def test_clean_code_passes():
    assert scan_security("def add(a, b):\n    return a + b\n") == []
