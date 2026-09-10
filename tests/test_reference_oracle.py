from table_iv_replication.reference_oracle import (
    BEHAVIOR_MISMATCH,
    EQUIVALENT_ON_DOMAIN,
    INCONCLUSIVE,
    SOURCE_IDENTICAL,
    compare_outputs,
    compare_references,
)

DOUBLE = "def f(x):\n    return x * 2\n"
ADD_SELF = "def f(x):\n    return x + x\n"
TRIPLE = "def f(x):\n    return x * 3\n"
DIVIDE = "def f(x):\n    return 1 / x\n"
SPIN = "def f(x):\n    while x:\n        pass\n    return 0\n"


def compare(out, exp, *, entry_point="f", args=(), atol=0.0):
    return compare_outputs(out, exp, entry_point=entry_point, args=args, atol=atol)


# --- the comparator mirrors evalplus 0.3.1 --------------------------------

def test_comparator_reuses_evalplus_helpers_rather_than_reimplementing_them():
    import evalplus.eval
    import evalplus.eval._special_oracle
    from table_iv_replication import _output_oracle

    names = _output_oracle.compare_outputs.__code__.co_names
    assert evalplus.eval.is_floats("x") is False  # upstream helper is importable
    assert callable(evalplus.eval._special_oracle._poly)
    # The comparator calls upstream's helpers by name; it does not carry copies.
    assert "is_floats" in names
    assert "_poly" in names
    assert "allclose" in names


def test_exact_equality_accepts():
    assert compare(3, 3) == (True, "exact equality")
    assert compare([1, "a"], [1, "a"])[0] is True


def test_float_results_get_the_1e_6_floor_even_when_atol_is_zero():
    accepted, reason = compare(0.1 + 0.2, 0.3)
    assert accepted is True
    assert "allclose" in reason


def test_unequal_non_floats_are_rejected_with_no_tolerance():
    accepted, reason = compare(3, 4)
    assert accepted is False
    assert "atol == 0" in reason


def test_type_and_length_mismatches_are_rejected():
    assert compare((1.0, 2.0), [1.0, 2.0]) == (
        False,
        "type mismatch: tuple vs list",
    )
    assert compare([1.0, 2.0], [1.0, 2.0, 3.0])[1] == "length mismatch: 2 vs 3"


def test_find_zero_special_oracle_accepts_any_root():
    coefficients = [-6, 11, -6, 1]  # roots at 1, 2 and 3
    accepted, reason = compare(
        1.0, 3.0, entry_point="find_zero", args=[coefficients], atol=1e-4
    )
    assert accepted is True
    assert "find_zero" in reason
    assert compare(1.5, 3.0, entry_point="find_zero", args=[coefficients], atol=1e-4)[0] is False


# --- whole-program comparison ---------------------------------------------

def test_identical_sources_short_circuit_without_executing():
    verdict = compare_references("T/0", DOUBLE, DOUBLE, "f", [[1], [2]])
    assert verdict.classification == SOURCE_IDENTICAL
    assert verdict.source_identical is True
    assert verdict.inputs_tested == 0


def test_different_sources_that_agree_on_the_domain():
    verdict = compare_references("T/1", DOUBLE, ADD_SELF, "f", [[1], [2], [3]])
    assert verdict.classification == EQUIVALENT_ON_DOMAIN
    assert verdict.inputs_tested == 3
    assert verdict.mismatches == []
    assert verdict.failures == []


def test_behaviour_mismatch_reports_input_and_both_outcomes():
    verdict = compare_references("T/2", DOUBLE, TRIPLE, "f", [[1], [2]])
    assert verdict.classification == BEHAVIOR_MISMATCH
    first = verdict.mismatches[0]
    assert first.index == 0
    assert first.args == "[1]"
    assert (first.left, first.right) == ("2", "3")
    assert "atol == 0" in first.reason


def test_matching_exceptions_are_the_same_observable_outcome():
    verdict = compare_references("T/3", DIVIDE, DIVIDE.replace("1 /", "2 /"), "f", [[0]])
    assert verdict.classification == EQUIVALENT_ON_DOMAIN
    verdict = compare_references("T/4", DIVIDE, DOUBLE, "f", [[0]])
    assert verdict.classification == BEHAVIOR_MISMATCH
    assert verdict.mismatches[0].kind == "exception"
    assert "one side raised" in verdict.mismatches[0].reason


def test_a_timeout_is_unobserved_not_a_behaviour_mismatch():
    # SPIN hangs on input [1] and returns 0 on [0], where DOUBLE agrees.
    verdict = compare_references("T/5", SPIN, DOUBLE, "f", [[1], [0]], timeout=0.3)

    assert verdict.classification == INCONCLUSIVE
    assert verdict.mismatches == []          # never claim a difference we did not see
    assert verdict.inputs_tested == 1        # only [0] could be compared
    assert [u.kind for u in verdict.unobserved] == ["timeout"]
    assert "could not be observed" in verdict.note


def test_a_pathological_reference_is_abandoned_after_the_timeout_budget():
    inputs = [[1]] * 12
    verdict = compare_references(
        "T/6", SPIN, DOUBLE, "f", inputs, timeout=0.2, timeout_budget=3
    )

    kinds = [u.kind for u in verdict.unobserved]
    assert verdict.classification == INCONCLUSIVE
    assert kinds.count("timeout") == 3       # budget spent
    assert kinds.count("skipped") == 9       # rest never executed
    assert len(verdict.unobserved) == len(inputs)
