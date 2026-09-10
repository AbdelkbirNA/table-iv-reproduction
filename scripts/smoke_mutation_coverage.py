"""Validate per-test mutation adequacy on HumanEval/0.

INFRASTRUCTURE VALIDATION ONLY. The HumanEval+ inputs used here are a smoke-test
pool, not the paper's table_iv_llm_plain_tests pool, and the canonical solution stands
in for a program under test. Nothing printed here is a Table IV result.

Provisional mutation engine: mutmut 3.7.0 (assumption A2, docs/experiment_plan.md).
"""

import sys
import time
from pathlib import Path

from evalplus.data import get_human_eval_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from table_iv_replication.mutation_coverage import (  # noqa: E402
    generate_mutants,
    measure_mutation_source,
    union_killed,
)

TASK_ID = "HumanEval/0"
PROBE_SIZE = 50
BUDGET_SECONDS = 300.0
TIMEOUT = 1.0


def main() -> int:
    problem = get_human_eval_plus()[TASK_ID]
    entry_point = problem["entry_point"]
    source = problem["prompt"] + problem["canonical_solution"]
    all_inputs = problem["base_input"] + problem["plus_input"]

    mutants, _ = generate_mutants(source, entry_point)

    print(f"Task: {TASK_ID}")
    print(f"Entry point: {entry_point}")
    print(f"Test inputs available: {len(all_inputs)}")
    print(f"Mutants generated: {len(mutants)} (mutmut 3.7.0, provisional engine)")
    print(f"Executions required: {len(all_inputs) * (len(mutants) + 2)} (mutant, input) pairs")

    print("\nGenerated mutants:")
    for mutant in mutants:
        print(f"  {mutant.id:36s} {mutant.description}")

    # Cost probe before committing to the full pool.
    probe_started = time.monotonic()
    measure_mutation_source(source, entry_point, all_inputs[:PROBE_SIZE], timeout=TIMEOUT)
    probe = time.monotonic() - probe_started
    projected = probe / PROBE_SIZE * len(all_inputs)
    print(
        f"\nCost probe: {PROBE_SIZE} inputs in {probe:.2f}s "
        f"-> full pool projected at {projected:.1f}s (budget {BUDGET_SECONDS:.0f}s)"
    )
    if projected > BUDGET_SECONDS:
        print("Projection exceeds budget: measuring the probe subset only.")
        inputs = all_inputs[:PROBE_SIZE]
    else:
        inputs = all_inputs

    run = measure_mutation_source(source, entry_point, inputs, timeout=TIMEOUT)

    print(f"\nInputs measured: {len(run.observations)}")
    print("\nMutants killed by each of the first 10 tests:")
    for observation in run.observations[:10]:
        killed = sorted(observation.killed, key=lambda mid: int(mid.rsplit("_", 1)[1]))
        print(
            f"  #{observation.index:<4d} original={observation.original} "
            f"kills={len(killed)} {[mid.rsplit('__mutmut_', 1)[1] for mid in killed]}"
        )

    distinct = {observation.killed for observation in run.observations}
    print(f"\nDistinct kill sets: {len(distinct)}")
    for killed in sorted(distinct, key=lambda s: (-len(s), sorted(s))):
        count = sum(1 for o in run.observations if o.killed == killed)
        short = sorted((mid.rsplit("__mutmut_", 1)[1] for mid in killed), key=int)
        print(f"  {len(killed)} killed {short} -> {count} tests")

    print(f"\nFull-pool killed mutants ({len(run.killed)}):")
    for mutant in run.mutants:
        if mutant.id in run.killed:
            print(f"  KILLED   {mutant.id:36s} {mutant.description}")
    print(f"\nObservational survivors ({len(run.survivors)}):")
    for mutant in run.mutants:
        if mutant.id in run.survivors:
            print(f"  SURVIVED {mutant.id:36s} {mutant.description}")
    if not run.survivors:
        print("  (none)")
    print("  NOTE: survivors are not proven equivalent mutants -- only unkilled")
    print("        by this pool. See assumption A3 in docs/experiment_plan.md.")

    timeouts = sum(
        1
        for observation in run.observations
        for outcome in observation.outcomes.values()
        if outcome.kind == "timeout"
    )
    print(f"\nExecution failures (worker crash/stall): {len(run.failures)}")
    for mutant_id, index, outcome in run.failures[:5]:
        print(f"  {mutant_id} on input #{index}: {outcome}")
    print(f"Per-call timeouts recorded as outcomes: {timeouts}")
    print(f"Trampoline baseline mismatches: {len(run.baseline_mismatches)}")
    print(f"Runtime: {run.elapsed:.2f}s")

    checks = [
        ("mutants were generated", bool(run.mutants), "no mutants"),
        (
            "per-test kill sets are subsets of the mutant set",
            all(o.killed <= set(run.mutant_ids) for o in run.observations),
            "unknown mutant id in a kill set",
        ),
        (
            "full-pool adequacy equals the union of per-test kills",
            union_killed(run.observations) == run.killed,
            "union mismatch",
        ),
        ("killed and survivors partition the mutants", run.killed | run.survivors == set(run.mutant_ids)
            and not (run.killed & run.survivors), "partition broken"),
        ("no worker crash or stall", not run.failures, f"{len(run.failures)} failures"),
        (
            "mutmut's trampoline preserves original behaviour",
            not run.baseline_mismatches,
            f"{len(run.baseline_mismatches)} mismatching inputs",
        ),
        ("tests differ in what they kill", len(distinct) > 1, "all tests kill the same set"),
    ]
    print("\nValidation:")
    ok = True
    for name, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + ("" if passed else f" -- {detail}"))
        ok = ok and passed

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
