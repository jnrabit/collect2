"""Tests für Qwythos-Tools, Collector und Validator."""

import json
from pathlib import Path

import pytest

from collect.qwythos.tools import TOOL_DEFINITIONS, call_tool, compute_schema_hash, SYSTEM_PROMPT


def test_tool_definitions_valid():
    assert len(TOOL_DEFINITIONS) == 7
    names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    assert names == {"retrieve", "translate", "decompose", "rewrite",
                     "classify", "answer", "done"}


def test_schema_hash_deterministic():
    h1 = compute_schema_hash()
    h2 = compute_schema_hash()
    assert h1 == h2
    assert len(h1) == 16


def test_system_prompt_not_empty():
    assert len(SYSTEM_PROMPT) > 100
    assert "Tool" in SYSTEM_PROMPT or "tool" in SYSTEM_PROMPT.lower()


def test_call_tool_unknown():
    result = call_tool("nonexistent", {})
    data = json.loads(result)
    assert "error" in data


def test_call_tool_retrieve_empty():
    result = call_tool("retrieve", {"query": ""})
    data = json.loads(result)
    assert "error" in data


def test_call_tool_translate_empty():
    result = call_tool("translate", {"query": ""})
    data = json.loads(result)
    assert data.get("translated") == ""


def test_call_tool_classify():
    result = call_tool("classify", {"query": "Was ist TLS?"})
    data = json.loads(result)
    assert data["classification"] in ("knowledge", "plan", "code")


def test_call_tool_classify_plan():
    result = call_tool("classify", {"query": "erstelle einen Plan für ein neues Projekt"})
    data = json.loads(result)
    assert data["classification"] == "plan"


def test_call_tool_classify_code():
    result = call_tool("classify", {"query": "code: implementiere eine fibonacci Funktion"})
    data = json.loads(result)
    assert data["classification"] == "code"


def test_call_tool_done():
    result = call_tool("done", {})
    data = json.loads(result)
    assert data["status"] == "done"
