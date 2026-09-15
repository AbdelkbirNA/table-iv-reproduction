"""Domain splitting and pool assembly for the Table IV protocol.

The paper draws on two *different* LLM-generated suites and this repository has
always kept them apart (``docs/experiment_plan.md``):

``fault_discovery_augmented_tests``
    defines the fault corpus and each fault's difficulty.
``table_iv_llm_plain_tests``
    the pool ``TS_f`` that Table IV samples adequate suites from.

We possess neither. What we do possess is one real input domain per task
(EvalPlus HumanEval+). Using it for both roles would collapse the separation the
paper relies on: a fault's difficulty would then be measured over the very pool
its suites are drawn from, and the retention rule would leak into the sampling.

So we split the domain **disjointly and deterministically** into a discovery
half and a pool half (assumption A8). Neither half is an LLM-generated suite;
the split only preserves the paper's separation of roles.

Nothing here executes code. It consumes already-measured per-test facts.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence

from .metrics import suite_detects_fault, suite_triggers_fault
from .sampling import derive_seed, run_repetitions
from .types import TestObservation

__all__ = [
    "REFERENCE_DIFFERENTIAL",
    "build_observations",
    "domain_difficulty",
    "select_coverage_pool",
    "split_domain",
    "sweep_pool_sizes",
]

#: Oracle regime of the observations this module builds. Each "test" is an input
#: plus the reference implementation's output as its expectation. Such an oracle
#: is correct by construction, so it flags every fault the input triggers and
#: ``detects_fault`` equals ``triggers_fault``. FDR measured under it is an
#: **upper bound** on the paper's FDR, which uses oracles an LLM wrote while
#: looking at the faulty program. See assumption A9.
REFERENCE_DIFFERENTIAL = "reference_differential"


def split_domain(
    size: int,
    *,
    seed: int,
    discovery_fraction: float = 0.5,
) -> tuple[list[int], list[int]]:
    """Split ``range(size)`` into disjoint (discovery, pool) index lists.

    Deterministic for a given ``seed``. Both halves are returned sorted so a
    downstream run can be compared index-by-index against the full domain.
    """
    if size < 0:
        raise ValueError("size must be non-negative")
    if not 0.0 < discovery_fraction < 1.0:
        raise ValueError("discovery_fraction must lie strictly between 0 and 1")

    indices = list(range(size))
    random.Random(seed).shuffle(indices)
    cut = int(round(size * discovery_fraction))
    return sorted(indices[:cut]), sorted(indices[cut:])


def domain_difficulty(triggered: int, observed: int) -> float | None:
    """Fraction of the observed inputs that do **not** trigger the fault.

    Mirrors the paper's difficulty direction: a fault triggered by at most 25%
    of the suite has difficulty >= 0.75 and is retained. ``None`` when nothing
    was observed, which is not the same as difficulty 0.
    """
    if observed <= 0:
        return None
    if not 0 <= triggered <= observed:
        raise ValueError("triggered must lie within [0, observed]")
    return 1.0 - triggered / observed


def select_coverage_pool(
    coverage_by_test: Mapping[str, frozenset[str]],
    *,
    target_size: int,
    seed: int,
) -> list[str]:
    """Build the sampling pool ``TS_f`` the way LLM-Plain is asked to (A8).

    LLM-Plain's generation prompt, read verbatim from the authors' own public
    implementation (``docs/llm_plain_reconstruction.md``), is *"generate all
    tests needed to achieve 100% code coverage"* with the program under test
    pasted into the prompt. Its output is therefore a small, coverage-oriented
    suite -- the paper reports 4,872 tests over 520 HumanEval faults, ~9.4 per
    fault -- and it is written **without sight of the reference solution**.

    This selection mirrors that objective on real inputs: greedily take the
    input adding the most new coverage until nothing adds any, then pad with
    further inputs up to ``target_size``, because LLM-Plain emits redundant
    tests too. Selection reads coverage only. It never reads whether an input
    triggers the fault, so a triggering input enters the pool only incidentally
    -- exactly as it would with a generator that cannot see the reference.

    Whether the triggering input survives into such a pool is itself a
    measurement, and it is the mechanism Table IV is about: coverage-directed
    test selection does not select *for* fault exposure.

    Ties are broken by a seeded shuffle, so the result is deterministic without
    privileging the domain's own ordering.
    """
    if target_size <= 0:
        raise ValueError("target_size must be positive")

    order = list(coverage_by_test)
    random.Random(seed).shuffle(order)

    selected: list[str] = []
    covered: set[str] = set()
    remaining = list(order)

    while remaining and len(selected) < target_size:
        best = max(remaining, key=lambda t: len(coverage_by_test[t].difference(covered)))
        if not coverage_by_test[best].difference(covered):
            break
        selected.append(best)
        covered.update(coverage_by_test[best])
        remaining.remove(best)

    # Coverage saturates well before the paper's density on small HumanEval
    # programs; pad so the pool size is comparable rather than artificially tiny.
    for test_id in remaining:
        if len(selected) >= target_size:
            break
        selected.append(test_id)

    return selected


def build_observations(
    adequacy_by_test: Mapping[str, frozenset[str]],
    triggering_tests: Iterable[str],
    *,
    oracle: str = REFERENCE_DIFFERENTIAL,
) -> list[TestObservation]:
    """Assemble one criterion's pool from measured adequacy and trigger facts.

    ``adequacy_by_test`` maps test id -> that criterion's adequacy items
    (statement lines, branch arcs, or killed mutants), measured against the
    faulty program. ``triggering_tests`` are the ids whose output differs from
    the reference.
    """
    if oracle != REFERENCE_DIFFERENTIAL:
        raise ValueError(f"unsupported oracle regime: {oracle!r}")

    triggering = set(triggering_tests)
    unknown = triggering.difference(adequacy_by_test)
    if unknown:
        raise ValueError(f"triggering tests absent from the pool: {sorted(unknown)[:5]}")

    return [
        TestObservation(
            test_id=test_id,
            adequacy_items=items,
            triggers_fault=test_id in triggering,
            # A9: a correct oracle detects exactly what it triggers.
            detects_fault=test_id in triggering,
        )
        for test_id, items in adequacy_by_test.items()
    ]


def _rates_for_pool(
    observations: Sequence[TestObservation],
    *,
    repetitions: int,
    seed: int,
) -> tuple[float, float, float]:
    """Mean trigger rate, detection rate and suite size over ``repetitions``."""
    if not observations:
        return 0.0, 0.0, 0.0
    suites = run_repetitions(observations, repetitions=repetitions, seed=seed)
    triggered = sum(suite_triggers_fault(s) for s in suites)
    detected = sum(suite_detects_fault(s) for s in suites)
    sizes = sum(len(s) for s in suites)
    return triggered / len(suites), detected / len(suites), sizes / len(suites)


def sweep_pool_sizes(
    observations_by_fault: Mapping[str, Sequence[TestObservation]],
    sizes: Sequence[int],
    *,
    draws: int,
    repetitions: int,
    seed: int,
) -> list[dict[str, float | int]]:
    """Measure how FTR/FDR move with the size of the sampling pool.

    Table IV's absolute FTR depends on how big ``TS_f`` is: a coverage-adequate
    suite retains only a few tests, so the chance it keeps a rare triggering one
    falls as the pool grows. The paper's pool averages ~9.4 tests per fault
    (4,872 tests / 520 faults) while an EvalPlus half-domain holds hundreds, so
    the two are not comparable at face value. This sweep measures that
    dependence instead of asserting it.

    For each size, ``draws`` independent sub-pools are drawn per fault and the
    full 100-iteration protocol is run on each; results are averaged over draws.
    A size at or above a fault's pool uses that whole pool once (drawing is then
    a no-op) so the largest size reproduces the headline run exactly.
    """
    if draws <= 0:
        raise ValueError("draws must be positive")

    rows: list[dict[str, float | int]] = []
    for size in sizes:
        if size <= 0:
            raise ValueError("pool sizes must be positive")
        ftrs: list[float] = []
        fdrs: list[float] = []
        suite_sizes: list[float] = []
        pool_sizes: list[int] = []
        for draw in range(draws):
            per_fault_ftr: list[float] = []
            per_fault_fdr: list[float] = []
            per_fault_size: list[float] = []
            for fault_id, observations in observations_by_fault.items():
                if size >= len(observations):
                    sub: Sequence[TestObservation] = observations
                    if draw > 0:  # whole pool: further draws would be identical
                        continue
                else:
                    rng = random.Random(derive_seed(seed + draw, f"pool|{fault_id}|{size}"))
                    sub = rng.sample(list(observations), size)
                ftr, fdr, mean_size = _rates_for_pool(
                    sub,
                    repetitions=repetitions,
                    seed=derive_seed(seed + draw, f"{fault_id}|{size}"),
                )
                per_fault_ftr.append(ftr)
                per_fault_fdr.append(fdr)
                per_fault_size.append(mean_size)
                pool_sizes.append(len(sub))
            if not per_fault_ftr:
                continue
            ftrs.append(sum(per_fault_ftr) / len(per_fault_ftr))
            fdrs.append(sum(per_fault_fdr) / len(per_fault_fdr))
            suite_sizes.append(sum(per_fault_size) / len(per_fault_size))
        if not ftrs:
            continue
        rows.append(
            {
                "requested_pool_size": size,
                "mean_pool_size": sum(pool_sizes) / len(pool_sizes),
                "draws_used": len(ftrs),
                "ftr": sum(ftrs) / len(ftrs),
                "fdr": sum(fdrs) / len(fdrs),
                "mean_suite_size": sum(suite_sizes) / len(suite_sizes),
            }
        )
    return rows
