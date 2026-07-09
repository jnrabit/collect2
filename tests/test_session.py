"""Tests für Session-Store und MeetingProtokoll."""

import time
from pathlib import Path

import pytest

from collect.session import SessionStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "sessions.db"
    return SessionStore(db)


def test_create_session(store):
    s = store.create("test-1", "Test Session")
    assert s["id"] == "test-1"
    assert s["title"] == "Test Session"
    assert s["turn_count"] == 0


def test_list_sessions(store):
    store.create("a", "Session A")
    store.create("b", "Session B")
    sessions = store.list_sessions()
    assert len(sessions) == 2


def test_get_session_empty(store):
    assert store.get("nonexistent") is None


def test_add_turn(store):
    store.create("test-2")
    store.add_turn("test-2", "Was ist Python?", "Python ist...",
                   zone="TRUST", best_distance=10.0, duration_s=1.5)
    s = store.get("test-2")
    assert len(s["turns"]) == 1
    assert s["turns"][0]["query"] == "Was ist Python?"
    assert s["turns"][0]["zone"] == "TRUST"


def test_get_turns_most_recent(store):
    store.create("test-3")
    for i in range(10):
        store.add_turn("test-3", f"Q{i}", f"A{i}")
    turns = store.get_turns("test-3", limit=3)
    assert len(turns) == 3
    assert turns[0]["q"] == "Q7"
    assert turns[-1]["q"] == "Q9"


def test_turn_count(store):
    store.create("test-4")
    assert store.turn_count("test-4") == 0
    store.add_turn("test-4", "Q", "A")
    assert store.turn_count("test-4") == 1


def test_summary(store):
    store.create("test-5")
    assert store.get_summary("test-5") is None
    store.set_summary("test-5", "Kernfakten: A, B, C", 5)
    assert store.get_summary("test-5") == "Kernfakten: A, B, C"


def test_needs_summary_under_limit(store):
    store.create("test-6")
    for i in range(3):
        store.add_turn("test-6", f"Q{i}", f"A{i}")
    assert not store.needs_summary("test-6")


def test_needs_summary_over_limit(store):
    store.create("test-7")
    for i in range(8):
        store.add_turn("test-7", f"Q{i}", f"A{i}")
    assert store.needs_summary("test-7")


def test_needs_summary_with_existing_summary(store):
    store.create("test-8")
    for i in range(8):
        store.add_turn("test-8", f"Q{i}", f"A{i}")
    store.set_summary("test-8", "Summary", 5)
    assert store.needs_summary("test-8")
    store.set_summary("test-8", "Summary", 8)
    assert not store.needs_summary("test-8")


def test_delete_session(store):
    store.create("test-9")
    store.add_turn("test-9", "Q", "A")
    store.delete("test-9")
    assert store.get("test-9") is None
    assert store.turn_count("test-9") == 0


def test_build_context_with_summary(store):
    store.create("test-10")
    store.add_turn("test-10", "Q1", "A1")
    store.set_summary("test-10", "Summary text", 1)
    ctx = store.build_context("test-10")
    assert ctx[0]["q"] == "_summary"
    assert ctx[0]["a"] == "Summary text"
    assert ctx[1]["q"] == "Q1"


def test_build_context_without_summary(store):
    store.create("test-11")
    store.add_turn("test-11", "Q1", "A1")
    ctx = store.build_context("test-11")
    assert ctx[0]["q"] == "Q1"
