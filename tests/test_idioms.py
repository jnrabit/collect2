"""Tests für Idiom-System und Workflow-Integration."""

import pytest

from collect.workflow.idioms import detect_task_type, get_idiom, inject_idiom, list_categories


def test_detect_task_type_function():
    assert detect_task_type("schreibe eine fibonacci funktion") == "function"
    assert detect_task_type("implement a method for sorting") == "function"


def test_detect_task_type_regex():
    assert detect_task_type("erstelle eine regex für emails") == "regex"
    assert detect_task_type("parse the pattern with regex") == "regex"


def test_detect_task_type_class():
    assert detect_task_type("erstelle eine Klasse User") == "class"
    assert detect_task_type("create a dataclass for config") == "class"


def test_detect_task_type_file_io():
    assert detect_task_type("lies eine json datei") == "file_io"
    assert detect_task_type("write csv output") == "file_io"


def test_detect_task_type_test():
    assert detect_task_type("schreibe tests für fibonacci") == "test"


def test_detect_task_type_algorithm():
    assert detect_task_type("binary search algorithmus") == "algorithm"


def test_detect_task_type_string():
    assert detect_task_type("slugify a text string") == "string"


def test_detect_task_type_general():
    assert detect_task_type("hello world") == "general"
    assert detect_task_type("xyz abc") == "general"


def test_list_categories():
    cats = list_categories()
    assert "function" in cats
    assert "test" in cats
    assert len(cats) >= 10


def test_inject_idiom_adds_example():
    prompt = "Schreibe Code."
    result = inject_idiom(prompt, "erstelle eine regex für emails", "execution")
    assert "BEISPIEL" in result
    assert len(result) > len(prompt)


def test_inject_idiom_unchanged_for_unknown_type():
    prompt = "hello"
    result = inject_idiom(prompt, "xyz abc unknown task", "nonexistent_phase")
    assert result == prompt
