import json
import pathlib

import pytest

from table_iv_replication.llm_plain_protocol import (
    ALTERNATIVE_SENSITIVITY_CONFIG,
    MODULE_LEVEL_TEST_NAME,
    PUBLIC_JAVA_PLAIN,
    PUBLIC_YATE_FAITHFUL,
    TABLE_IV_AS_PUBLISHED,
    GeneratedTestSuite,
    LLMPlainConversation,
    Provenance,
    RepairIteration,
    UnknownParameter,
    build_python_generation_prompt,
    build_python_repair_prompt,
    build_python_system_prompt,
    extract_code_blocks,
    extract_test_code,
    split_test_cases,
)

PROGRAM = "def add(a, b):\n    return a - b\n"

TEST_CODE = '''from mod import add


def helper():
    return 1


def test_positive():
    assert add(2, 3) == 5


def test_zero():
    assert add(0, 0) == 0
'''


# --- provenance ------------------------------------------------------------

def test_public_java_values_are_tagged_as_read_from_source():
    assert PUBLIC_JAVA_PLAIN.generation_temperature == 0.1
    assert PUBLIC_JAVA_PLAIN.max_repair_iterations == 5
    assert PUBLIC_JAVA_PLAIN.uses_coverage_feedback is False
    for name in ("generation_temperature", "max_repair_iterations", "uses_coverage_feedback"):
        assert PUBLIC_JAVA_PLAIN.provenance_of(name) is Provenance.CONFIRMED_PUBLIC_IMPLEMENTATION


def test_table_iv_parameters_are_unknown_and_cannot_be_defaulted():
    unknown = TABLE_IV_AS_PUBLISHED.unknown_fields()
    assert "generation_temperature" in unknown
    assert "max_repair_iterations" in unknown
    assert "model_name" in unknown
    assert "test_framework" in unknown

    with pytest.raises(UnknownParameter, match="must not be defaulted"):
        TABLE_IV_AS_PUBLISHED.require("generation_temperature")
    with pytest.raises(UnknownParameter):
        TABLE_IV_AS_PUBLISHED.require("max_repair_iterations")

    # What the paper does state is known.
    assert TABLE_IV_AS_PUBLISHED.require("language") == "Python"
    assert TABLE_IV_AS_PUBLISHED.provenance_of("language") is Provenance.CONFIRMED_TARGET_PAPER


def test_reconstruction_follows_the_public_repair_policy():
    """Corrected: the public implementation repairs whatever the runner surfaces,
    assertion failures included, so the faithful arm does too."""
    config = PUBLIC_YATE_FAITHFUL

    assert config.repairs_assertion_failures is True
    assert config.provenance_of("repairs_assertion_failures") is (
        Provenance.CONFIRMED_PUBLIC_IMPLEMENTATION
    )
    # But the paper never says so, and the two facts travel together.
    assert config.target_paper_status("repairs_assertion_failures") is (
        Provenance.UNKNOWN_TARGET_PAPER_IMPLEMENTATION
    )
    assert not config.confirmed_by_target_paper("repairs_assertion_failures")
    assert "UNKNOWN" in config.notes["repairs_assertion_failures"]
    # Values taken from the Java source are tagged as such, not as adaptations.
    for name in ("generation_temperature", "max_repair_iterations"):
        assert config.provenance_of(name) is Provenance.CONFIRMED_PUBLIC_IMPLEMENTATION
        assert not config.confirmed_by_target_paper(name)
    # Our own choice stays an adaptation; the model is still undecided.
    assert config.provenance_of("test_framework") is Provenance.INFERRED_ADAPTATION
    assert not config.is_known("model_name")


def test_sensitivity_arm_turns_assertion_repair_off_as_our_own_choice():
    arm = ALTERNATIVE_SENSITIVITY_CONFIG

    assert arm.repairs_assertion_failures is False
    assert arm.provenance_of("repairs_assertion_failures") is Provenance.INFERRED_ADAPTATION
    assert "FDR" in arm.notes["repairs_assertion_failures"]
    # Everything else still carries its public-implementation provenance.
    assert arm.provenance_of("generation_temperature") is (
        Provenance.CONFIRMED_PUBLIC_IMPLEMENTATION
    )
    assert arm.max_repair_iterations == PUBLIC_YATE_FAITHFUL.max_repair_iterations


def test_the_paper_status_of_a_field_is_never_changed_by_our_choices():
    derived = PUBLIC_YATE_FAITHFUL.with_values(generation_temperature=0.9)
    assert derived.provenance_of("generation_temperature") is Provenance.INFERRED_ADAPTATION
    assert derived.target_paper_status("generation_temperature") is (
        Provenance.UNKNOWN_TARGET_PAPER_IMPLEMENTATION
    )


def test_derived_config_downgrades_overridden_fields_to_adaptation():
    derived = PUBLIC_JAVA_PLAIN.with_values(generation_temperature=0.7)
    assert derived.generation_temperature == 0.7
    assert derived.provenance_of("generation_temperature") is Provenance.INFERRED_ADAPTATION
    assert derived.provenance_of("max_repair_iterations") is Provenance.CONFIRMED_PUBLIC_IMPLEMENTATION


# --- prompt construction ---------------------------------------------------

def test_generation_prompt_is_a_single_minimal_message():
    prompt = build_python_generation_prompt(PROGRAM)
    assert prompt.startswith("The following module is missing unit tests.")
    assert "100% code coverage" in prompt
    assert "Return only the code" in prompt
    assert prompt.endswith(PROGRAM)               # program under test inlined
    # "Plain" means plain: no elicitation the public prompt does not use.
    for absent in ("step by step", "First,", "Think", "example:", "branch coverage"):
        assert absent not in prompt


def test_system_prompt_mirrors_the_public_one_with_python():
    assert build_python_system_prompt() == (
        "You are a tool used by Python Developers to generate tests."
    )


def test_repair_prompt_echoes_the_tests_not_the_program():
    prompt = build_python_repair_prompt("def test_x():\n    assert False\n", "E   assert False")
    assert "This is my current implementation." in prompt
    assert "def test_x()" in prompt
    assert "E   assert False" in prompt
    assert "Respond only with the fixed code." in prompt
    assert PROGRAM not in prompt


def test_generation_prompt_refuses_an_unknown_framework():
    with pytest.raises(UnknownParameter):
        build_python_generation_prompt(PROGRAM, TABLE_IV_AS_PUBLISHED)


# --- response parsing ------------------------------------------------------

def test_raw_code_without_fences_is_returned_as_is():
    assert extract_test_code(TEST_CODE) == TEST_CODE.strip()


def test_python_fenced_block_is_preferred():
    response = f"Sure, here you go:\n\n```python\n{TEST_CODE}```\n\nHope that helps!"
    code = extract_test_code(response)
    assert code == TEST_CODE.strip()
    assert "Hope that helps" not in code


def test_bare_fence_is_handled_where_the_java_regex_would_return_prose():
    response = f"Here:\n\n```\n{TEST_CODE}```\n"
    assert extract_test_code(response) == TEST_CODE.strip()


def test_first_block_wins_and_other_languages_are_ignored_when_python_exists():
    response = (
        "```bash\npytest -q\n```\n\n"
        f"```python\n{TEST_CODE}```\n\n"
        "```python\ndef test_second():\n    assert True\n```\n"
    )
    code = extract_test_code(response)
    assert "test_positive" in code
    assert "test_second" not in code
    assert [block.language for block in extract_code_blocks(response)] == [
        "bash", "python", "python",
    ]


def test_unterminated_final_fence_is_still_extracted():
    assert "test_positive" in extract_test_code(f"```python\n{TEST_CODE}")


def test_malformed_and_empty_responses_do_not_raise():
    assert extract_test_code("") is None
    assert extract_test_code("   \n  ") is None
    assert extract_test_code("```python\n```") is None
    assert extract_test_code("I cannot help with that.") == "I cannot help with that."


def test_splitting_keeps_the_preamble_with_each_test():
    cases, error = split_test_cases(TEST_CODE)
    assert error is None
    assert [case.name for case in cases] == ["test_positive", "test_zero"]
    assert "from mod import add" in cases[0].preamble
    assert "def helper()" in cases[0].preamble
    assert "test_zero" not in cases[0].source
    module = cases[0].runnable_module
    assert module.startswith("from mod import add")
    assert "def test_positive()" in module
    assert "def test_zero()" not in module


def test_syntax_error_is_reported_not_raised_and_nothing_is_executed(tmp_path):
    marker = tmp_path / "executed"
    broken = f"import pathlib\npathlib.Path({str(marker)!r}).touch()\ndef test_x(:\n    pass\n"
    cases, error = split_test_cases(broken)
    assert cases == []
    assert "SyntaxError" in error
    assert not marker.exists()


def test_parsing_never_executes_valid_code_either(tmp_path):
    marker = tmp_path / "executed"
    code = f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n\n\ndef test_x():\n    assert True\n"
    cases, error = split_test_cases(code)

    assert error is None
    # The top-level call is captured as a module-level check, not buried in the
    # preamble where it would re-run for every other test.
    assert [case.name for case in cases] == ["test_x", MODULE_LEVEL_TEST_NAME]
    assert cases[1].module_level is True
    assert "touch()" not in cases[0].preamble
    # ...and parsing did not run it.
    assert not marker.exists()


def test_top_level_asserts_are_kept_and_never_duplicated_into_the_preamble():
    code = (
        "from solution import classify\n\n"
        "def helper():\n    return 1\n\n"
        "assert classify(1) == 10\n"
        "assert classify(-1) == 20\n\n"
        "def test_named():\n    assert classify(0) == 20\n"
    )
    cases, error = split_test_cases(code)
    by_name = {case.name: case for case in cases}

    assert error is None
    assert set(by_name) == {"test_named", MODULE_LEVEL_TEST_NAME}
    module_level = by_name[MODULE_LEVEL_TEST_NAME]
    assert module_level.module_level is True
    assert module_level.source.splitlines() == [
        "assert classify(1) == 10",
        "assert classify(-1) == 20",
    ]
    # The preamble carries imports and helpers only -- no assertions.
    for case in cases:
        assert "assert" not in case.preamble
        assert "from solution import classify" in case.preamble
        assert "def helper()" in case.preamble
    # A named test does not inherit the module-level assertions.
    assert "classify(1)" not in by_name["test_named"].runnable_module


# --- suite records ---------------------------------------------------------

def build_suite():
    response = f"Here are the tests:\n\n```python\n{TEST_CODE}```\n"
    conversation = (
        LLMPlainConversation.start(build_python_system_prompt())
        .ask(build_python_generation_prompt(PROGRAM))
        .answer(response)
    )
    return GeneratedTestSuite.from_response(
        suite_id="s1",
        task_id="HumanEval/0",
        fault_id="HumanEval/0|original",
        config=PUBLIC_YATE_FAITHFUL,
        system_prompt=build_python_system_prompt(),
        generation_prompt=build_python_generation_prompt(PROGRAM),
        raw_response=response,
        conversation=conversation,
    )


def test_suite_preserves_the_raw_response_verbatim():
    suite = build_suite()
    assert "Here are the tests:" in suite.raw_response
    assert suite.extracted_code == TEST_CODE.strip()
    assert [case.name for case in suite.test_cases] == ["test_positive", "test_zero"]
    assert suite.syntax_error is None
    assert "RECONSTRUCTION" in suite.provenance_label
    # The raw response is enough to re-derive everything else.
    assert extract_test_code(suite.raw_response) == suite.extracted_code


def test_conversation_keeps_one_system_message_and_can_discard_a_codeless_reply():
    conversation = LLMPlainConversation.start("sys").ask("p1").answer("r1")
    assert [m.role for m in conversation.messages] == ["system", "user", "assistant"]
    # Mirrors the Java behaviour: a reply with no extractable code is not kept.
    dropped = conversation.ask("p2").answer("sorry", keep=False)
    assert [m.role for m in dropped.messages] == ["system", "user", "assistant", "user"]


def test_serialization_is_deterministic():
    first, second = build_suite().to_dict(), build_suite().to_dict()
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["config"]["provenance"]["generation_temperature"] == (
        "CONFIRMED_PUBLIC_IMPLEMENTATION"
    )
    assert first["config"]["target_paper"]["generation_temperature"] == (
        "UNKNOWN_TARGET_PAPER_IMPLEMENTATION"
    )


def test_repair_iteration_round_trips():
    repair = RepairIteration(
        index=0, errors="E   assert False", raw_response="```python\nfixed\n```",
        extracted_code="fixed", accepted=True,
    )
    assert json.loads(json.dumps(repair.to_dict()))["accepted"] is True


# --- the module must not be able to call an LLM ----------------------------

def test_protocol_module_contains_no_api_client():
    import table_iv_replication.llm_plain_protocol as protocol

    source = pathlib.Path(protocol.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import openai", "from openai", "import anthropic", "from anthropic",
        "import requests", "import httpx", "urllib.request", "http.client",
        "socket", "API_KEY", "api_key",
    ):
        assert forbidden not in source, f"{forbidden!r} must not appear in the protocol module"
