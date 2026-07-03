"""Decision: Grammatik, JSON-Parsing, Decompose mit gemocktem LLM."""

from collect.agents.decision import (
    decide_plan_steps,
    is_executable_action,
    parse_plan_steps,
)


def test_is_executable_action():
    assert is_executable_action("read:config.py")
    assert is_executable_action("write:pfad|inhalt")
    assert is_executable_action("execute:python x.py")
    assert is_executable_action("list_files")           # bare erlaubt
    assert is_executable_action("list_files:data/")     # mit arg erlaubt
    assert not is_executable_action("")
    assert not is_executable_action("read:")            # arg fehlt
    assert not is_executable_action("überlege dir was")  # freie Prosa


def test_parse_plan_steps_robust_against_chatter():
    raw = ('Klar, hier der Plan:\n'
           '[{"id":"step_1","description":"Dateien listen","action":"list_files:data/"},'
           ' {"id":"step_2","description":"Nachdenken","action":""}]\n'
           'Viel Erfolg!')
    steps = parse_plan_steps(raw)
    assert len(steps) == 2
    assert steps[0]["executable"] is True
    assert steps[1]["executable"] is False


def test_parse_plan_steps_garbage_returns_empty():
    assert parse_plan_steps("") == []
    assert parse_plan_steps("kein json hier") == []
    assert parse_plan_steps('{"kein": "array"}') == []


def test_parse_caps_at_max_steps():
    raw = "[" + ",".join(
        f'{{"id":"s{i}","description":"d{i}","action":""}}' for i in range(10)) + "]"
    assert len(parse_plan_steps(raw)) == 6  # settings.max_plan_steps


def test_decide_with_mocked_llm():
    def fake_generate(prompt, system="", timeout=None):
        return '[{"id":"step_1","description":"lesen","action":"read:README.md"}]'
    result = decide_plan_steps("Lies die README", generate_fn=fake_generate)
    assert result["actionable"] is True
    assert result["steps"][0]["action"] == "read:README.md"


def test_decide_llm_failure_falls_back_to_informational():
    def broken(prompt, system="", timeout=None):
        raise RuntimeError("ollama weg")
    result = decide_plan_steps("Mach was", generate_fn=broken)
    assert result["actionable"] is False
    assert len(result["steps"]) == 1
    assert result["steps"][0]["description"] == "Mach was"
