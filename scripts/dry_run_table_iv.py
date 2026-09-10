"""End-to-end dry run of the Table IV pipeline on synthetic artifacts.

No LLM, no network, no benchmark data. Hand-written "generated" tests stand in
for LLM output. The point is not to match the paper -- it is to prove every link
in the chain is connected and deterministic:

    generated test text
      -> parse into individual tests
      -> execute in isolation, record entry-point invocations
      -> trigger status (does an input distinguish faulty from reference?)
      -> detection status (does the test's own oracle flag it?)
      -> adequacy items, measured for real (statement / branch / mutation)
      -> randomized adequate-suite sampling, 100 iterations
      -> suite trigger/detection
      -> FTR / FDR means

    python scripts/dry_run_table_iv.py [--seed N] [--repetitions N]
"""

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from table_iv_replication import branch_coverage, statement_coverage  # noqa: E402
from table_iv_replication.generated_test_runner import (  # noqa: E402
    run_generated_tests,
    to_test_observation,
)
from table_iv_replication.llm_plain_protocol import split_test_cases  # noqa: E402
from table_iv_replication.metrics import aggregate_rates  # noqa: E402
from table_iv_replication.mutation_coverage import measure_mutation_source  # noqa: E402
from table_iv_replication.sampling import full_pool_adequacy, run_repetitions  # noqa: E402

ENTRY_POINT = "classify"

REFERENCE = """def classify(x):
    if x > 0:
        return 10
    if x == 0:
        return 15
    return 20
"""

#: Three synthetic faults. Each carries the "generated" test pool for that fault
#: and, per test, the inputs we expect it to exercise -- declared so the run can
#: verify the runtime instrumentation instead of trusting it.
FAULTS = {
    "fault_a_positive_branch": {
        "source": """def classify(x):
    if x > 0:
        return 11
    if x == 0:
        return 15
    return 20
""",
        "tests": """def test_positive_strict():
    assert classify(1) == 10


def test_positive_weak():
    assert classify(2) in (10, 11)


def test_zero():
    assert classify(0) == 15


def test_negative():
    assert classify(-3) == 20
""",
        "inputs": {
            "test_positive_strict": [[1]],
            "test_positive_weak": [[2]],
            "test_zero": [[0]],
            "test_negative": [[-3]],
        },
    },
    "fault_b_zero_branch": {
        "source": """def classify(x):
    if x > 0:
        return 10
    if x == 0:
        return 16
    return 20
""",
        "tests": """def test_positive():
    assert classify(5) == 10


def test_zero_biased():
    assert classify(0) == 16


def test_negative():
    assert classify(-1) == 20
""",
        "inputs": {
            "test_positive": [[5]],
            "test_zero_biased": [[0]],
            "test_negative": [[-1]],
        },
    },
    "fault_c_negative_branch": {
        "source": """def classify(x):
    if x > 0:
        return 10
    if x == 0:
        return 15
    return 21
""",
        "tests": """def test_walk_all_branches():
    assert classify(1) == 10
    assert classify(0) == 15
    assert classify(-1) == 20


def test_positive_only():
    assert classify(4) == 10
""",
        "inputs": {
            "test_walk_all_branches": [[1], [0], [-1]],
            "test_positive_only": [[4]],
        },
    },
}


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def adequacy_for_tests(source: str, tests_inputs: dict[str, list], timeout: float):
    """Real statement, branch and mutation adequacy per test, on the faulty program.

    A test's adequacy is the union over every input it exercises -- the same
    definition used everywhere else in the project.
    """
    every_input = [args for inputs in tests_inputs.values() for args in inputs]
    index_of = {}
    flat = []
    for args in every_input:
        key = repr(args)
        if key not in index_of:
            index_of[key] = len(flat)
            flat.append(args)

    statements, _ = statement_coverage.measure_source(source, ENTRY_POINT, flat)
    branches, _ = branch_coverage.measure_source(source, ENTRY_POINT, flat)
    mutation = measure_mutation_source(source, ENTRY_POINT, flat, timeout=timeout)

    items = {}
    for name, inputs in tests_inputs.items():
        indices = [index_of[repr(args)] for args in inputs]
        items[name] = {
            "statement": frozenset().union(
                *(statements[i].adequacy_items for i in indices)
            ),
            "branch": frozenset().union(*(branches[i].adequacy_items for i in indices)),
            "mutation": frozenset().union(
                *(mutation.observations[i].adequacy_items for i in indices)
            ),
        }
    return items, mutation


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args(argv)

    print("SYNTHETIC DRY RUN -- no LLM, no benchmark data, no paper comparison.")
    print(f"Seed {args.seed}, {args.repetitions} sampling iterations per criterion.")

    criteria = ("statement", "branch", "mutation")
    observations: dict[str, dict[str, list]] = {name: {} for name in criteria}
    behaviour: dict[str, list] = {}

    for fault_id, fault in FAULTS.items():
        rule(f"FAULT {fault_id}")
        tests, error = split_test_cases(fault["tests"])
        assert error is None, error

        results = run_generated_tests(
            REFERENCE,
            fault["source"],
            ENTRY_POINT,
            tests,
            fault_id=fault_id,
            timeout=args.timeout,
        )
        behaviour[fault_id] = results

        print(f"{'test':<26}{'ref':<10}{'faulty':<20}{'trig':<7}{'det':<6}verdict")
        for result in results:
            print(
                f"{result.test_name:<26}{result.reference_run.status.value:<10}"
                f"{result.faulty_run.status.value:<20}"
                f"{str(result.triggered):<7}{str(result.detects_fault):<6}"
                f"{result.verdict.value}"
            )

        # Verify the runtime instrumentation against the declared inputs.
        for result in results:
            recorded = [call.args for call in result.reference_run.invocations]
            expected = [repr(tuple(args)) for args in fault["inputs"][result.test_name]]
            assert recorded == expected, (result.test_name, recorded, expected)
        print("instrumentation check: recorded invocations match declared inputs")

        items, mutation = adequacy_for_tests(fault["source"], fault["inputs"], args.timeout)
        print(f"mutants generated: {len(mutation.mutants)}, "
              f"killed by the pool: {len(mutation.killed)}, "
              f"observational survivors: {len(mutation.survivors)}")
        print(f"{'test':<26}{'statement':<12}{'branch':<10}mutation")
        for result in results:
            per_test = items[result.test_name]
            print(
                f"{result.test_name:<26}{len(per_test['statement']):<12}"
                f"{len(per_test['branch']):<10}{len(per_test['mutation'])}"
            )
            for criterion in criteria:
                observations[criterion].setdefault(fault_id, []).append(
                    to_test_observation(result, per_test[criterion])
                )

    rule("FULL-POOL ADEQUACY PER FAULT")
    print(f"{'fault':<26}{'criterion':<12}{'pool items':<12}tests")
    for criterion in criteria:
        for fault_id, pool in observations[criterion].items():
            print(f"{fault_id:<26}{criterion:<12}"
                  f"{len(full_pool_adequacy(pool)):<12}{len(pool)}")

    def sample(criterion, seed):
        """Mean FTR/FDR/suite size over `repetitions` randomized suites."""
        suites_by_fault = {
            fault_id: run_repetitions(pool, repetitions=args.repetitions, seed=seed)
            for fault_id, pool in observations[criterion].items()
        }
        ftrs, fdrs, sizes, detect_implies_trigger = [], [], [], True
        for iteration in range(args.repetitions):
            iteration_suites = {
                fault_id: suites[iteration] for fault_id, suites in suites_by_fault.items()
            }
            ftr, fdr = aggregate_rates(iteration_suites)
            ftrs.append(ftr)
            fdrs.append(fdr)
            sizes.extend(len(suite) for suite in iteration_suites.values())
            detect_implies_trigger &= fdr <= ftr
        return (
            statistics.fmean(ftrs),
            statistics.fmean(fdrs),
            statistics.fmean(sizes),
            detect_implies_trigger,
        )

    rule(f"RANDOMIZED SAMPLING -- {args.repetitions} ITERATIONS PER CRITERION")
    summary = {}
    print(f"{'criterion':<12}{'mean FTR':>10}{'mean FDR':>10}{'mean suite size':>18}")
    for criterion in criteria:
        summary[criterion] = sample(criterion, args.seed)
        print(f"{criterion:<12}{summary[criterion][0]:>10.4f}"
              f"{summary[criterion][1]:>10.4f}{summary[criterion][2]:>18.4f}")

    # Prove determinism rather than asserting it: same seed, same numbers.
    repeated = {criterion: sample(criterion, args.seed) for criterion in criteria}

    rule("PIPELINE CHECKS")
    checks = [
        (
            "every criterion sampled the same fault set",
            all(set(observations[c]) == set(FAULTS) for c in criteria),
        ),
        (
            "behaviour is identical across criteria (only adequacy differs)",
            all(
                [(o.triggers_fault, o.detects_fault) for o in observations["statement"][f]]
                == [(o.triggers_fault, o.detects_fault) for o in observations[c][f]]
                for c in criteria
                for f in FAULTS
            ),
        ),
        (
            "at least one criterion yields a different mean FTR than another",
            len({round(summary[c][0], 6) for c in criteria}) > 1
            or len({round(summary[c][2], 6) for c in criteria}) > 1,
        ),
        (
            "detection implies triggering for every observation",
            all(
                o.triggers_fault
                for criterion in criteria
                for pool in observations[criterion].values()
                for o in pool
                if o.detects_fault
            ),
        ),
        (
            "FDR never exceeds FTR in any iteration",
            all(summary[criterion][3] for criterion in criteria),
        ),
        (
            "rerunning with the same seed reproduces every number",
            repeated == summary,
        ),
    ]
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    print("\nDeterminism: rerun with the same --seed to reproduce every number above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
