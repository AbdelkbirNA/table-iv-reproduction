from __future__ import annotations

import random
from collections.abc import Iterable, Sequence

from .types import TestObservation


def full_pool_adequacy(tests: Iterable[TestObservation]) -> frozenset[str]:
    covered: set[str] = set()
    for test in tests:
        covered.update(test.adequacy_items)
    return frozenset(covered)


def greedy_random_sample(
    tests: Sequence[TestObservation],
    *,
    rng: random.Random,
) -> list[TestObservation]:
    """Sample a suite using the paper's randomized coverage-increase protocol.

    We randomize test order and retain a test iff it adds at least one new
    adequacy item. Sampling stops once the selected suite reaches the adequacy
    of the full test pool.
    """
    if not tests:
        return []

    target = full_pool_adequacy(tests)
    candidates = list(tests)
    rng.shuffle(candidates)

    selected: list[TestObservation] = []
    covered: set[str] = set()

    for test in candidates:
        gain = test.adequacy_items.difference(covered)
        if gain:
            selected.append(test)
            covered.update(test.adequacy_items)
        if covered == target:
            break

    if covered != target:
        raise RuntimeError("Failed to reach full-pool adequacy")

    return selected


def run_repetitions(
    tests: Sequence[TestObservation],
    *,
    repetitions: int = 100,
    seed: int = 0,
) -> list[list[TestObservation]]:
    """Run independent randomized suite selections with deterministic seeds."""
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")

    master = random.Random(seed)
    suites: list[list[TestObservation]] = []
    for _ in range(repetitions):
        suites.append(greedy_random_sample(tests, rng=random.Random(master.getrandbits(64))))
    return suites
