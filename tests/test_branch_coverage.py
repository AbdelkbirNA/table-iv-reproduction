from table_iv_replication.branch_coverage import (
    branch_points,
    measure_one,
    measure_source,
    possible_branch_arcs,
    union_arcs,
)
from table_iv_replication.target import target_program

# Line 2 is sequential, line 3 is the only decision point.
SOURCE = """def classify(x):
    y = x * 2
    if y > 0:
        return 1
    return -1
"""

LOOP_SOURCE = """def first_negative(values):
    for value in values:
        if value < 0:
            return value
    return None
"""


def test_static_branch_points_and_outcomes():
    with target_program(SOURCE, "classify") as (path, _):
        assert branch_points(path) == frozenset({3})
        assert possible_branch_arcs(path) == frozenset({(3, 4), (3, 5)})


def test_branches_expose_different_adequacy_items():
    observations, possible = measure_source(SOURCE, "classify", [[1], [-1]])
    true_branch, false_branch = observations

    assert [o.result for o in observations] == [1, -1]
    assert true_branch.arcs == frozenset({(3, 4)})
    assert false_branch.arcs == frozenset({(3, 5)})
    assert true_branch.arcs != false_branch.arcs
    assert union_arcs(observations) == possible


def test_sequential_arcs_are_not_adequacy_items():
    with target_program(SOURCE, "classify") as (path, fn):
        observation = measure_one(fn, [1], path)
        # coverage.py did record the sequential flow through lines 1-2-3-4...
        assert observation.lines == frozenset({2, 3, 4})
        # ...but only the arc leaving the decision point is an adequacy item.
        assert observation.arcs == frozenset({(3, 4)})
        assert (2, 3) not in observation.arcs
        assert all(origin in branch_points(path) for origin, _ in observation.arcs)


def test_loop_back_edge_is_a_real_branch_outcome():
    observations, possible = measure_source(
        LOOP_SOURCE, "first_negative", [[[]], [[1, 2]], [[1, -2]]]
    )
    empty, no_hit, hit = observations
    assert possible == frozenset({(2, 3), (2, 5), (3, 2), (3, 4)})
    assert empty.arcs == frozenset({(2, 5)})
    assert no_hit.arcs == frozenset({(2, 3), (2, 5), (3, 2)})
    assert hit.arcs == frozenset({(2, 3), (3, 2), (3, 4)})
    assert union_arcs(observations) == possible


def test_only_the_target_file_is_measured():
    with target_program(SOURCE, "classify") as (path, fn):
        observation = measure_one(fn, [1], path)
        assert observation.measured_files == frozenset({str(path.resolve())})


def test_runtime_error_is_recorded_not_hidden():
    with target_program(SOURCE, "classify") as (path, fn):
        observation = measure_one(fn, ["nan"], path)
        assert observation.error is not None
        assert "TypeError" in observation.error
        # The exception escaping line 3 is recorded by coverage.py as (3, -1),
        # which is not a statically possible branch outcome: kept visible,
        # never counted as adequacy.
        assert observation.arcs == frozenset()
        assert observation.discarded_arcs == frozenset({(3, -1)})


def test_adequacy_items_are_stable_strings():
    observations, _ = measure_source(SOURCE, "classify", [[1]])
    assert observations[0].adequacy_items == frozenset({"3->4"})


def test_full_pool_never_targets_an_impossible_outcome():
    observations, possible = measure_source(SOURCE, "classify", [[1], [-1], ["nan"]])
    assert union_arcs(observations) == possible
    assert all(arc in possible for o in observations for arc in o.arcs)
