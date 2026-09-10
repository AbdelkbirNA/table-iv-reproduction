import random

from table_iv_replication.sampling import full_pool_adequacy, greedy_random_sample, run_repetitions
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
