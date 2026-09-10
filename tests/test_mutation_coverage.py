import glob
import os
import tempfile

import pytest

from table_iv_replication.mutation_coverage import (
    generate_mutants,
    measure_mutation_source,
    union_killed,
)

CLASSIFY = """def classify(x):
    if x > 0:
        return x + 1
    return x - 1
"""

# n == 1 leaves the mutated loop bound unreachable -> a mutant that never stops.
COUNTDOWN = """def countdown(n):
    total = 0
    while n > 0:
        total += n
        n -= 1
    return total
"""


def test_mutants_are_generated_with_stable_unique_ids():
    mutants, module_source = generate_mutants(CLASSIFY, "classify")
    ids = [m.id for m in mutants]

    assert len(mutants) >= 1
    assert len(set(ids)) == len(ids)
    assert ids == [m.id for m in generate_mutants(CLASSIFY, "classify")[0]]
    assert all(m.function == "classify" for m in mutants)
    # Locations are reported against the caller's own source numbering.
    assert {m.source_line for m in mutants} == {2, 3, 4}
    assert "_mutmut_select" in module_source


def test_generated_module_has_no_runtime_dependency_on_mutmut():
    _, module_source = generate_mutants(CLASSIFY, "classify")
    assert "from mutmut" not in module_source
    assert "import mutmut" not in module_source


def test_unknown_entry_point_is_rejected():
    with pytest.raises(ValueError, match="no function named"):
        generate_mutants(CLASSIFY, "not_a_function")


def test_different_tests_kill_different_mutants():
    run = measure_mutation_source(CLASSIFY, "classify", [[5], [0], [-5]])
    positive, boundary, negative = run.observations

    assert positive.killed != negative.killed
    assert boundary.killed - positive.killed  # the >= boundary mutant
    assert union_killed(run.observations) == run.killed
    assert run.killed == frozenset().union(*(o.killed for o in run.observations))
    assert run.baseline_mismatches == []
    assert run.failures == []


def test_survivors_are_reported_and_not_called_equivalent():
    run = measure_mutation_source(CLASSIFY, "classify", [[5], [0], [-5]])
    ids = {m.id for m in run.mutants}

    assert run.survivors == ids - run.killed
    assert run.survivors  # `x > 1` needs x == 1, which this pool never supplies
    # Adding the missing input kills it: survival was observational, not equivalence.
    wider = measure_mutation_source(CLASSIFY, "classify", [[5], [0], [-5], [1]])
    assert run.survivors <= wider.killed


def test_exception_is_an_outcome_not_an_error():
    run = measure_mutation_source(CLASSIFY, "classify", [["bad"]])
    observation = run.observations[0]

    assert observation.original.kind == "exception"
    assert observation.original.value == "TypeError"
    # Every mutant raises the same TypeError -> same observable outcome -> no kill.
    assert observation.killed == frozenset()


def test_timeout_is_a_kill_and_does_not_freeze_the_process():
    run = measure_mutation_source(COUNTDOWN, "countdown", [[3]], timeout=0.3)
    observation = run.observations[0]
    timed_out = {mid for mid, o in observation.outcomes.items() if o.kind == "timeout"}

    assert timed_out  # `n = 1` / `n += 1` never terminate
    assert timed_out <= observation.killed
    assert run.elapsed < 30


def test_adequacy_items_are_exactly_the_killed_mutant_ids():
    run = measure_mutation_source(CLASSIFY, "classify", [[5]])
    observation = run.observations[0]
    ids = {m.id for m in run.mutants}

    assert observation.adequacy_items == observation.killed
    assert observation.adequacy_items <= ids
    assert all(isinstance(item, str) for item in observation.adequacy_items)


def test_nothing_is_written_outside_the_temporary_workspace():
    cwd = os.getcwd()
    before = sorted(os.listdir(cwd))
    leftover_before = glob.glob(os.path.join(tempfile.gettempdir(), "table_iv_*"))

    source = CLASSIFY
    measure_mutation_source(source, "classify", [[5], [-5]])

    assert source == CLASSIFY  # caller's source untouched
    assert os.getcwd() == cwd  # mutmut's chdir was restored
    assert sorted(os.listdir(cwd)) == before  # no mutants/ directory
    assert glob.glob(os.path.join(tempfile.gettempdir(), "table_iv_*")) == leftover_before


CLASS_TARGET = """class Box:
    def scale(self, x):
        return x * 2


def entry(x):
    return Box().scale(x) + 1
"""


def test_methods_inside_classes_are_mutated_and_executable():
    run = measure_mutation_source(CLASS_TARGET, "entry", [[3], [0]])
    functions = {m.function for m in run.mutants}

    assert functions == {"scale", "entry"}
    assert any(m.id.startswith("xǁBoxǁscale") for m in run.mutants)
    assert run.observations[0].original.value == "7"
    assert run.killed  # helper-function mutants are part of the program under test
    assert run.baseline_mismatches == []
