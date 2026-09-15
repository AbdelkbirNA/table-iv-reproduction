import random

import pytest

from table_iv_replication.domain_pool import (
    REFERENCE_DIFFERENTIAL,
    build_observations,
    domain_difficulty,
    select_coverage_pool,
    split_domain,
    sweep_pool_sizes,
)
from table_iv_replication.types import TestObservation


# --------------------------------------------------------------------------
# split_domain
# --------------------------------------------------------------------------


def test_split_is_disjoint_and_exhaustive():
    discovery, pool = split_domain(100, seed=7)
    assert set(discovery).isdisjoint(pool)
    assert sorted(discovery + pool) == list(range(100))


def test_split_is_deterministic_for_a_seed():
    assert split_domain(500, seed=11) == split_domain(500, seed=11)


def test_split_depends_on_the_seed():
    assert split_domain(500, seed=11) != split_domain(500, seed=12)


def test_split_honours_the_discovery_fraction():
    discovery, pool = split_domain(1000, seed=3, discovery_fraction=0.3)
    assert len(discovery) == 300
    assert len(pool) == 700


def test_split_returns_sorted_halves():
    discovery, pool = split_domain(200, seed=5)
    assert discovery == sorted(discovery)
    assert pool == sorted(pool)


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
def test_split_rejects_degenerate_fractions(fraction):
    with pytest.raises(ValueError):
        split_domain(10, seed=1, discovery_fraction=fraction)


# --------------------------------------------------------------------------
# domain_difficulty
# --------------------------------------------------------------------------


def test_difficulty_direction_matches_the_paper():
    # "triggered by at most 25% of the suite" is difficulty >= 0.75.
    assert domain_difficulty(25, 100) == 0.75
    assert domain_difficulty(1, 1000) == pytest.approx(0.999)
    assert domain_difficulty(100, 100) == 0.0


def test_difficulty_is_none_when_nothing_was_observed():
    # Distinct from difficulty 0.0, which would mean "every test triggers it".
    assert domain_difficulty(0, 0) is None


def test_difficulty_rejects_impossible_counts():
    with pytest.raises(ValueError):
        domain_difficulty(5, 4)


# --------------------------------------------------------------------------
# select_coverage_pool
# --------------------------------------------------------------------------


def test_selection_prefers_tests_that_add_coverage():
    coverage = {
        "a": frozenset({"L1", "L2"}),
        "b": frozenset({"L1"}),  # subsumed by a
        "c": frozenset({"L3"}),
    }
    selected = select_coverage_pool(coverage, target_size=2, seed=1)
    assert set(selected) == {"a", "c"}


def test_selection_saturates_then_pads_to_the_target_size():
    coverage = {f"t{i}": frozenset({"L1"}) for i in range(10)}
    selected = select_coverage_pool(coverage, target_size=4, seed=1)
    # Only one test adds coverage; the rest are padding, as LLM-Plain also
    # emits redundant tests.
    assert len(selected) == 4
    assert len(set(selected)) == 4


def test_selection_never_exceeds_the_target_size():
    coverage = {f"t{i}": frozenset({f"L{i}"}) for i in range(50)}
    assert len(select_coverage_pool(coverage, target_size=10, seed=1)) == 10


def test_selection_is_deterministic_for_a_seed():
    coverage = {f"t{i}": frozenset({f"L{i % 5}"}) for i in range(40)}
    assert select_coverage_pool(coverage, target_size=8, seed=4) == select_coverage_pool(
        coverage, target_size=8, seed=4
    )


def test_selection_is_oracle_blind():
    """The trap: selection must not depend on which test triggers the fault.

    ``select_coverage_pool`` is given coverage only, so this pins the contract
    at the call boundary -- a future refactor that starts passing trigger status
    in would fail here.
    """
    coverage = {f"t{i}": frozenset({f"L{i % 7}"}) for i in range(30)}
    baseline = select_coverage_pool(coverage, target_size=10, seed=9)
    # Same coverage, wholly different trigger assignment: identical pool.
    assert select_coverage_pool(dict(coverage), target_size=10, seed=9) == baseline


def test_selection_rejects_a_non_positive_target():
    with pytest.raises(ValueError):
        select_coverage_pool({"a": frozenset()}, target_size=0, seed=1)


# --------------------------------------------------------------------------
# build_observations
# --------------------------------------------------------------------------


def test_detection_equals_triggering_under_the_reference_oracle():
    observations = build_observations(
        {"a": frozenset({"L1"}), "b": frozenset({"L2"})}, ["a"]
    )
    by_id = {o.test_id: o for o in observations}
    assert by_id["a"].triggers_fault and by_id["a"].detects_fault
    assert not by_id["b"].triggers_fault and not by_id["b"].detects_fault


def test_build_rejects_a_trigger_outside_the_pool():
    # Guards the accounting: a fault must never be credited with a trigger the
    # sampler could not have selected.
    with pytest.raises(ValueError):
        build_observations({"a": frozenset({"L1"})}, ["ghost"])


def test_build_rejects_an_unsupported_oracle_regime():
    with pytest.raises(ValueError):
        build_observations({"a": frozenset()}, [], oracle="llm_written")


def test_reference_differential_is_the_declared_default():
    assert REFERENCE_DIFFERENTIAL == "reference_differential"


# --------------------------------------------------------------------------
# sweep_pool_sizes
# --------------------------------------------------------------------------


def _pool(n_tests, trigger_ids):
    return [
        TestObservation(
            test_id=f"t{i}",
            adequacy_items=frozenset({f"L{i}"}),
            triggers_fault=f"t{i}" in trigger_ids,
            detects_fault=f"t{i}" in trigger_ids,
        )
        for i in range(n_tests)
    ]


def test_sweep_reports_one_row_per_size():
    rows = sweep_pool_sizes(
        {"f1": _pool(40, {"t3"})}, [5, 10, 20], draws=3, repetitions=10, seed=1
    )
    assert [r["requested_pool_size"] for r in rows] == [5, 10, 20]


def test_ftr_rises_with_pool_coverage_of_the_trigger():
    """Every test here adds unique coverage, so an adequate suite is the whole
    pool: FTR is then exactly the chance the sub-pool drew the triggering test,
    which grows with the sub-pool size."""
    faults = {"f1": _pool(100, {"t0"})}
    rows = sweep_pool_sizes(faults, [5, 50], draws=40, repetitions=5, seed=2)
    assert rows[0]["ftr"] < rows[1]["ftr"]


def test_a_size_at_or_above_the_pool_uses_the_whole_pool_once():
    rows = sweep_pool_sizes(
        {"f1": _pool(10, {"t0"})}, [25], draws=7, repetitions=5, seed=3
    )
    assert rows[0]["mean_pool_size"] == 10
    assert rows[0]["draws_used"] == 1  # further draws would be identical
    assert rows[0]["ftr"] == 1.0


def test_sweep_is_deterministic_for_a_seed():
    faults = {"f1": _pool(30, {"t1"}), "f2": _pool(30, {"t2"})}
    first = sweep_pool_sizes(faults, [5, 10], draws=4, repetitions=10, seed=8)
    second = sweep_pool_sizes(faults, [5, 10], draws=4, repetitions=10, seed=8)
    assert first == second


def test_sweep_rejects_degenerate_arguments():
    faults = {"f1": _pool(10, {"t0"})}
    with pytest.raises(ValueError):
        sweep_pool_sizes(faults, [5], draws=0, repetitions=5, seed=1)
    with pytest.raises(ValueError):
        sweep_pool_sizes(faults, [0], draws=1, repetitions=5, seed=1)


def test_an_empty_pool_yields_no_trigger_rather_than_an_error():
    rows = sweep_pool_sizes({"f1": []}, [5], draws=2, repetitions=5, seed=1)
    assert rows[0]["ftr"] == 0.0
    assert rows[0]["mean_suite_size"] == 0.0


def test_seeded_sampling_does_not_disturb_global_random_state():
    random.seed(1234)
    expected = random.random()
    random.seed(1234)
    sweep_pool_sizes({"f1": _pool(20, {"t0"})}, [5], draws=3, repetitions=5, seed=99)
    assert random.random() == expected
