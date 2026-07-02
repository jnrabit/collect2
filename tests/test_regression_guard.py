"""Regression-Guard-Kernlogik pinnen (reine Funktionen, kein git nötig)."""

from collect.regression_guard import (
    check_change,
    top_level_symbols,
    verdict_for,
)

OLD = '''\
CONST = 1

def keep():
    pass

def gone():
    pass

class Thing:
    pass
'''

NEW_SYMBOL_LOSS = '''\
CONST = 1

def keep():
    pass

class Thing:
    pass
'''


def test_top_level_symbols():
    assert top_level_symbols(OLD) == {"CONST", "keep", "gone", "Thing"}


def test_top_level_symbols_syntax_error_returns_none():
    assert top_level_symbols("def broken(:") is None


def test_symbol_loss_detected():
    issues = check_change("m.py", OLD, NEW_SYMBOL_LOSS)
    assert any(i["kind"] == "symbol_loss" for i in issues)
    assert verdict_for(issues) == "🔴"


def test_symbol_loss_authorized_by_plan_text():
    issues = check_change("m.py", OLD, NEW_SYMBOL_LOSS,
                          plan_text="wir wollen gone entfernen, ist tot")
    assert not any(i["kind"] == "symbol_loss" for i in issues)


def test_size_collapse_is_yellow():
    old = "\n".join(f"x{i} = {i}" for i in range(40))
    new = "x0 = 0"
    issues = check_change("m.py", old, new)
    kinds = {i["kind"] for i in issues}
    assert "size_collapse" in kinds
    # alle Symbole weg → auch rot; hier nur den Kollaps-Pfad isoliert prüfen
    collapse_only = [i for i in issues if i["kind"] == "size_collapse"]
    assert verdict_for(collapse_only) == "🟡"


def test_no_issues_is_green():
    issues = check_change("m.py", OLD, OLD + "\ndef extra():\n    pass\n")
    assert issues == []
    assert verdict_for(issues) == "🟢"


def test_non_python_file_only_size_checked():
    old = "\n".join(["zeile"] * 40)
    issues = check_change("notes.md", old, "kurz")
    assert {i["kind"] for i in issues} == {"size_collapse"}
