import random

from table_iv_replication.sampling import (
    derive_seed,
    full_pool_adequacy,
    greedy_random_sample,
    run_repetitions,
)
from table_iv_replication.types import TestObservation


def obs(test_id, items, trigger=False, detect=False):
    return TestObservation(test_id, frozenset(items), trigger, detect)


def test_sample_reaches_full_pool_adequacy():
    tests = [
        obs("t1", {"s1"}),
        obs("t2", {"s1", "s2"}),
        obs("t3", {"s3"}),
    ]
    suite = greedy_random_sample(tests, rng=random.Random(7))
    assert full_pool_adequacy(suite) == full_pool_adequacy(tests)


def test_redundant_test_is_not_required():
    tests = [
        obs("t1", {"s1", "s2"}),
        obs("t2", {"s1"}),
        obs("t3", {"s2"}),
    ]
    suite = greedy_random_sample(tests, rng=random.Random(0))
    assert full_pool_adequacy(suite) == frozenset({"s1", "s2"})
    assert len(suite) <= 2


def test_repetitions_are_reproducible_for_same_seed():
    tests = [obs("a", {"x"}), obs("b", {"y"}), obs("c", {"x", "y"})]
    a = run_repetitions(tests, repetitions=10, seed=42)
    b = run_repetitions(tests, repetitions=10, seed=42)
    assert [[t.test_id for t in s] for s in a] == [[t.test_id for t in s] for s in b]


# --- sampler audit: rules the Table IV protocol depends on ------------------

def pool(prefix, size, items_per_test=1):
    return [
        obs(f"{prefix}{i}", {f"{prefix}_item{i}_{j}" for j in range(items_per_test)})
        for i in range(size)
    ]


def test_every_sampled_suite_is_minimal_and_duplicate_free():
    """A test is kept iff it added an item, and a discarded test never returns."""
    tests = [
        obs("t1", {"a", "b"}),
        obs("t2", {"a"}),
        obs("t3", {"b", "c"}),
        obs("t4", {"c"}),
    ]
    for suite in run_repetitions(tests, repetitions=50, seed=3):
        assert len({t.test_id for t in suite}) == len(suite)
        covered = set()
        for test in suite:
            assert test.adequacy_items - covered, "kept a test that added nothing"
            covered |= test.adequacy_items


def test_sampling_stops_exactly_at_full_pool_adequacy():
    tests = [obs("t1", {"a"}), obs("t2", {"b"}), obs("t3", {"a", "b"}), obs("t4", {"a"})]
    target = full_pool_adequacy(tests)
    for suite in run_repetitions(tests, repetitions=50, seed=11):
        assert full_pool_adequacy(suite) == target
        # No trailing test: once adequacy is reached the loop breaks, so the last
        # test selected is the one that completed the target.
        assert full_pool_adequacy(suite[:-1]) != target or len(suite) == 1


def test_suites_are_greedily_minimal_not_minimum_set_covers():
    """The protocol is a randomized greedy walk, not minimum set cover.

    Every retained test added an item *when it was selected*, but an earlier test
    can later become redundant -- [t1={a}, t3={a,b}] is a legitimate outcome even
    though {t3} alone is adequate. Suite sizes from this sampler are therefore not
    minimal, and must not be reported as if they were.
    """
    tests = [obs("t1", {"a"}), obs("t3", {"a", "b"})]
    suites = [
        [t.test_id for t in suite] for suite in run_repetitions(tests, repetitions=40, seed=2)
    ]
    assert ["t1", "t3"] in suites          # greedy: t1 first, then t3 adds "b"
    assert ["t3"] in suites                # t3 first: adequate on its own
    for suite in run_repetitions(tests, repetitions=40, seed=2):
        covered = set()
        for test in suite:
            assert test.adequacy_items - covered
            covered |= test.adequacy_items


def test_each_repetition_starts_from_an_empty_suite():
    """Suite contents must not accumulate across repetitions."""
    tests = [obs("t1", {"a"}), obs("t2", {"b"})]
    suites = run_repetitions(tests, repetitions=20, seed=5)
    assert all(len(suite) == 2 for suite in suites)      # never 4, never growing


def test_repetitions_are_independent_draws_within_a_fault():
    """100 repetitions must actually randomize, not repeat one permutation."""
    orders = {
        tuple(t.test_id for t in suite)
        for suite in run_repetitions(pool("t", 4), repetitions=100, seed=9)
    }
    assert len(orders) > 1


def test_same_base_seed_couples_equal_sized_pools_without_derived_seeds():
    """Documented trap: two faults sampled with the same seed share permutations.

    Marginal rates stay unbiased, but per-iteration rates across faults become
    correlated. This test pins the behaviour so the mitigation is not dropped.
    """
    a = run_repetitions(pool("a", 4), repetitions=10, seed=1)
    b = run_repetitions(pool("b", 4), repetitions=10, seed=1)
    positions_a = [[t.test_id[1:] for t in suite] for suite in a]
    positions_b = [[t.test_id[1:] for t in suite] for suite in b]
    assert positions_a == positions_b


def test_derive_seed_decouples_faults_and_stays_reproducible():
    a = run_repetitions(pool("a", 4), repetitions=10, seed=derive_seed(1, "fault_a"))
    b = run_repetitions(pool("b", 4), repetitions=10, seed=derive_seed(1, "fault_b"))
    assert [[t.test_id[1:] for t in s] for s in a] != [
        [t.test_id[1:] for t in s] for s in b
    ]
    # Same base and key -> same seed, every time and in every process.
    assert derive_seed(1, "fault_a") == derive_seed(1, "fault_a")
    assert derive_seed(1, "fault_a") != derive_seed(1, "fault_b")
    assert derive_seed(1, "fault_a") != derive_seed(2, "fault_a")


def test_a_test_with_no_adequacy_items_can_never_be_selected():
    """Consequence worth knowing: such a test can never contribute to FTR."""
    triggering_but_empty = obs("t_empty", set(), trigger=True, detect=True)
    tests = [obs("t1", {"a"}), triggering_but_empty]
    for suite in run_repetitions(tests, repetitions=20, seed=13):
        assert triggering_but_empty not in suite
