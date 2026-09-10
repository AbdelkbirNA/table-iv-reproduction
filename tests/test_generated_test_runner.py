"""Golden tests for the generated-test execution engine.

Deterministic, hand-written suites only. No LLM is involved anywhere here.
"""

import pytest

from table_iv_replication.generated_test_runner import (
    DetectionVerdict,
    TestStatus,
    classify_detection,
    run_generated_tests,
    summarize_results,
    to_test_observation,
)
from table_iv_replication.llm_plain_protocol import split_test_cases
from table_iv_replication.metrics import (
    aggregate_rates,
    suite_detects_fault,
    suite_triggers_fault,
)

REFERENCE = """def classify(x):
    if x > 0:
        return 10
    return 20
"""

FAULTY = """def classify(x):
    if x > 0:
        return 11
    return 20
"""


def run(code, **kwargs):
    """Parse a generated response and run every test it contains."""
    tests, error = split_test_cases(code)
    assert error is None, error
    return run_generated_tests(
        REFERENCE, FAULTY, "classify", tests, fault_id="F/classify", **kwargs
    )


def only(code, **kwargs):
    results = run(code, **kwargs)
    assert len(results) == 1, [r.test_name for r in results]
    return results[0]


# ---------------------------------------------------------------- golden cases

def test_case_1_correct_oracle_triggers_and_detects():
    result = only("def test_a():\n    assert classify(1) == 10\n")

    assert (result.triggered, result.detects_fault) == (True, True)
    assert result.verdict is DetectionVerdict.DETECTED
    assert result.reference_run.status is TestStatus.PASS
    assert result.faulty_run.status is TestStatus.ASSERTION_FAILURE
    assert result.triggering_inputs[0]["reference_outcome"] == "10"
    assert result.triggering_inputs[0]["faulty_outcome"] == "11"


def test_case_2_faulty_biased_oracle_triggers_without_detecting():
    result = only("def test_a():\n    assert classify(1) == 11\n")

    assert (result.triggered, result.detects_fault) == (True, False)
    assert result.verdict is DetectionVerdict.FAULTY_BIASED_ORACLE
    assert result.reference_run.status is TestStatus.ASSERTION_FAILURE
    assert result.faulty_run.status is TestStatus.PASS
    assert "faulty behaviour" in result.note


def test_case_3_input_that_does_not_reach_the_fault():
    result = only("def test_a():\n    assert classify(-1) == 20\n")

    assert (result.triggered, result.detects_fault) == (False, False)
    assert result.verdict is DetectionVerdict.NOT_TRIGGERED
    assert result.reference_run.status is TestStatus.PASS
    assert result.faulty_run.status is TestStatus.PASS


def test_case_4_weak_oracle_triggers_without_detecting():
    result = only("def test_a():\n    assert classify(1) in (10, 11)\n")

    assert (result.triggered, result.detects_fault) == (True, False)
    assert result.verdict is DetectionVerdict.TRIGGERED_NOT_DETECTED
    assert result.reference_run.status is TestStatus.PASS
    assert result.faulty_run.status is TestStatus.PASS


def test_case_5_multiple_target_calls_in_one_test():
    result = only(
        "def test_a():\n"
        "    assert classify(-1) == 20\n"
        "    assert classify(1) == 10\n"
    )

    assert (result.triggered, result.detects_fault) == (True, True)
    assert result.verdict is DetectionVerdict.DETECTED
    # Both calls recorded on the reference; the faulty run stops at the failure.
    assert [call.args for call in result.reference_run.invocations] == ["(-1,)", "(1,)"]
    assert result.distinct_inputs == 2
    # Only the second input distinguishes the programs.
    assert [item["args"] for item in result.triggering_inputs] == ["(1,)"]


def test_case_6_broken_test_neither_triggers_nor_detects():
    result = only("def test_a():\n    assert unknown_name == 5\n")

    assert (result.triggered, result.detects_fault) == (False, False)
    assert result.verdict is DetectionVerdict.BROKEN_TEST
    assert result.reference_run.status is TestStatus.RUNTIME_ERROR
    assert "NameError" in result.reference_run.error
    assert result.reference_run.invocations == ()      # never reached the target


def test_case_7_oracle_wrong_on_both_programs_is_invalid_not_detection():
    result = only("def test_a():\n    assert classify(1) == 999\n")

    assert result.detects_fault is False
    assert result.verdict is DetectionVerdict.INVALID_ORACLE
    assert result.reference_run.status is TestStatus.ASSERTION_FAILURE
    assert result.faulty_run.status is TestStatus.ASSERTION_FAILURE
    # It still triggers: the recorded input does distinguish the programs.
    assert result.triggered is True


def test_case_8_infinite_loop_times_out_without_hanging():
    result = only(
        "def test_a():\n"
        "    while True:\n"
        "        pass\n"
        "    assert classify(1) == 10\n",
        timeout=0.4,
    )

    assert result.verdict is DetectionVerdict.BROKEN_TEST
    assert result.reference_run.status is TestStatus.TIMEOUT
    assert result.faulty_run.status is TestStatus.TIMEOUT
    assert result.detects_fault is False


# ------------------------------------------------------- granularity handling

def test_multiple_test_functions_are_evaluated_separately():
    results = run(
        "def test_good():\n    assert classify(1) == 10\n\n"
        "def test_biased():\n    assert classify(1) == 11\n\n"
        "def test_safe():\n    assert classify(-1) == 20\n"
    )
    by_name = {result.test_name: result.verdict for result in results}

    assert by_name == {
        "test_good": DetectionVerdict.DETECTED,
        "test_biased": DetectionVerdict.FAULTY_BIASED_ORACLE,
        "test_safe": DetectionVerdict.NOT_TRIGGERED,
    }


def test_top_level_asserts_are_executed_not_dropped():
    result = only("assert classify(1) == 10\n")

    assert result.module_level is True
    assert result.verdict is DetectionVerdict.DETECTED
    assert result.detects_fault is True


def test_helper_functions_are_available_to_the_test():
    result = only(
        "def expected(x):\n"
        "    return 10 if x > 0 else 20\n\n"
        "def test_a():\n"
        "    assert classify(1) == expected(1)\n"
    )

    assert result.verdict is DetectionVerdict.DETECTED


def test_import_from_a_known_alias_works():
    result = only(
        "from solution import classify as f\n\n"
        "def test_a():\n"
        "    assert f(1) == 10\n"
    )

    assert result.verdict is DetectionVerdict.DETECTED
    assert result.reference_run.invocations[0].args == "(1,)"


def test_a_test_that_never_calls_the_entry_point_cannot_trigger():
    result = only("def test_a():\n    assert 1 + 1 == 2\n")

    assert result.triggered is False
    assert result.verdict is DetectionVerdict.NOT_TRIGGERED
    assert result.invocation_count == 0


def test_kwargs_are_recorded_and_replayed():
    result = only("def test_a():\n    assert classify(x=1) == 10\n")

    assert result.verdict is DetectionVerdict.DETECTED
    assert result.reference_run.invocations[0].kwargs == "{'x': 1}"


def test_no_tests_returns_no_results():
    assert run_generated_tests(REFERENCE, FAULTY, "classify", []) == []


# ------------------------------------------------- fault-induced crash policy

def test_a_faulty_program_crash_is_inconclusive_not_detection():
    crashing = "def classify(x):\n    if x > 0:\n        raise ValueError('boom')\n    return 20\n"
    tests, _ = split_test_cases("def test_a():\n    assert classify(1) == 10\n")
    (result,) = run_generated_tests(REFERENCE, crashing, "classify", tests)

    assert result.triggered is True                 # the outcomes do differ
    assert result.faulty_run.status is TestStatus.RUNTIME_ERROR
    assert result.verdict is DetectionVerdict.DETECTION_INCONCLUSIVE
    assert result.detects_fault is False           # conservative: no FDR credit


# ------------------------------------------------------ verdict table itself

@pytest.mark.parametrize(
    "reference, faulty, triggered, expected",
    [
        (TestStatus.PASS, TestStatus.ASSERTION_FAILURE, True, DetectionVerdict.DETECTED),
        (TestStatus.PASS, TestStatus.ASSERTION_FAILURE, False, DetectionVerdict.DETECTION_INCONCLUSIVE),
        (TestStatus.PASS, TestStatus.PASS, True, DetectionVerdict.TRIGGERED_NOT_DETECTED),
        (TestStatus.PASS, TestStatus.PASS, False, DetectionVerdict.NOT_TRIGGERED),
        (TestStatus.ASSERTION_FAILURE, TestStatus.ASSERTION_FAILURE, True, DetectionVerdict.INVALID_ORACLE),
        (TestStatus.ASSERTION_FAILURE, TestStatus.PASS, True, DetectionVerdict.FAULTY_BIASED_ORACLE),
        (TestStatus.RUNTIME_ERROR, TestStatus.PASS, False, DetectionVerdict.BROKEN_TEST),
        (TestStatus.INVALID_TEST, TestStatus.INVALID_TEST, False, DetectionVerdict.BROKEN_TEST),
        (TestStatus.TIMEOUT, TestStatus.PASS, False, DetectionVerdict.BROKEN_TEST),
        (TestStatus.PASS, TestStatus.RUNTIME_ERROR, True, DetectionVerdict.DETECTION_INCONCLUSIVE),
    ],
)
def test_verdict_table_is_explicit(reference, faulty, triggered, expected):
    verdict, note = classify_detection(reference, faulty, triggered)
    assert verdict is expected
    assert note


def test_only_detected_ever_counts_for_fdr():
    for verdict in DetectionVerdict:
        counts = verdict is DetectionVerdict.DETECTED
        assert counts == (verdict.value == "DETECTED")


# ------------------------------------------------ observation adapter + suites

def test_observation_adapter_accepts_any_criterion_items():
    result = only("def test_a():\n    assert classify(1) == 10\n")

    statement = to_test_observation(result, {"L2", "L3"})
    branch = to_test_observation(result, {"2->3"})
    mutation = to_test_observation(result, {"x_classify__mutmut_1"})

    assert statement.test_id == "F/classify|test_a"
    assert (statement.triggers_fault, statement.detects_fault) == (True, True)
    # Behaviour is identical across criteria; only the adequacy items differ.
    for observation in (statement, branch, mutation):
        assert observation.triggers_fault is True
        assert observation.detects_fault is True
    assert statement.adequacy_items == frozenset({"L2", "L3"})
    assert branch.adequacy_items == frozenset({"2->3"})
    assert mutation.adequacy_items == frozenset({"x_classify__mutmut_1"})


def test_suite_level_facts_are_any_not_all():
    results = run(
        "def test_weak():\n    assert classify(1) in (10, 11)\n\n"
        "def test_strong():\n    assert classify(1) == 10\n"
    )
    suite = [to_test_observation(result, {"L1"}) for result in results]

    assert suite_triggers_fault(suite) is True
    assert suite_detects_fault(suite) is True

    weak_only = [suite[[r.test_name for r in results].index("test_weak")]]
    assert suite_triggers_fault(weak_only) is True
    assert suite_detects_fault(weak_only) is False


def test_summary_counts_verdicts():
    summary = summarize_results(
        run(
            "def test_good():\n    assert classify(1) == 10\n\n"
            "def test_biased():\n    assert classify(1) == 11\n\n"
            "def test_safe():\n    assert classify(-1) == 20\n"
        )
    )
    assert summary["tests"] == 3
    assert summary["triggering"] == 2
    assert summary["detecting"] == 1
    assert summary["suite_triggers_fault"] is True
    assert summary["suite_detects_fault"] is True
    assert summary["verdicts"]["FAULTY_BIASED_ORACLE"] == 1


def test_ftr_and_fdr_across_three_synthetic_faults():
    """Two of three faults triggered, one of three detected."""
    detected = to_test_observation(
        only("def test_a():\n    assert classify(1) == 10\n"), {"L1"}
    )
    triggered_only = to_test_observation(
        only("def test_a():\n    assert classify(1) in (10, 11)\n"), {"L1"}
    )
    neither = to_test_observation(
        only("def test_a():\n    assert classify(-1) == 20\n"), {"L1"}
    )

    ftr, fdr = aggregate_rates({"f1": [detected], "f2": [triggered_only], "f3": [neither]})
    assert ftr == pytest.approx(2 / 3)
    assert fdr == pytest.approx(1 / 3)


# ------------------------------------------- deterministic 100-run sampling

def test_hundred_iteration_sampling_is_deterministic_and_criterion_dependent():
    """The whole chain, in miniature: behaviour once, adequacy per criterion."""
    from table_iv_replication.sampling import run_repetitions

    results = run(
        "def test_strong():\n    assert classify(1) == 10\n\n"
        "def test_weak():\n    assert classify(1) in (10, 11)\n\n"
        "def test_safe():\n    assert classify(-1) == 20\n"
    )
    by_name = {result.test_name: result for result in results}

    # Same behavioural facts, three different adequacy views.
    adequacy = {
        "statement": {"test_strong": {"L2"}, "test_weak": {"L2"}, "test_safe": {"L4"}},
        "branch": {"test_strong": {"2->3"}, "test_weak": {"2->3"}, "test_safe": {"2->4"}},
        "mutation": {"test_strong": {"m1", "m2"}, "test_weak": {"m1"}, "test_safe": {"m3"}},
    }

    outcomes = {}
    for criterion, items in adequacy.items():
        pool = [to_test_observation(by_name[name], values) for name, values in items.items()]
        suites = run_repetitions(pool, repetitions=100, seed=7)
        again = run_repetitions(pool, repetitions=100, seed=7)

        assert len(suites) == 100
        assert [[t.test_id for t in s] for s in suites] == [
            [t.test_id for t in s] for s in again
        ]
        ftr = sum(suite_triggers_fault(s) for s in suites) / 100
        fdr = sum(suite_detects_fault(s) for s in suites) / 100
        assert fdr <= ftr
        outcomes[criterion] = (ftr, fdr)

    # Every criterion must reach the fault here, but detection need not agree.
    assert all(ftr == 1.0 for ftr, _ in outcomes.values())
    assert outcomes["statement"][1] < 1.0     # the weak oracle can satisfy L2 alone
    assert outcomes["mutation"][1] == 1.0     # m2 forces the strong test in
