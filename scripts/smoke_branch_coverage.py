"""Validate per-test branch coverage on HumanEval/0.

Infrastructure validation only: the canonical implementation stands in for a
program under test. In the real reproduction, adequacy is measured against each
faulty implementation (see docs/experiment_plan.md).
"""

import sys
from pathlib import Path

from evalplus.data import get_human_eval_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from table_iv_replication.branch_coverage import (  # noqa: E402
    branch_points,
    measure_all,
    possible_branch_arcs,
    union_arcs,
)
from table_iv_replication.target import target_program  # noqa: E402

TASK_ID = "HumanEval/0"


def fmt(arcs) -> str:
    return "{" + ", ".join(f"{a}->{b}" for a, b in sorted(arcs)) + "}"


def main() -> int:
    problem = get_human_eval_plus()[TASK_ID]
    entry_point = problem["entry_point"]
    source = problem["prompt"] + problem["canonical_solution"]
    all_inputs = problem["base_input"] + problem["plus_input"]

    with target_program(source, entry_point, filename="humaneval_0_target.py") as (
        target,
        fn,
    ):
        points = branch_points(target)
        possible = possible_branch_arcs(target)

        print(f"Task: {TASK_ID}")
        print(f"Entry point: {entry_point}")
        print(f"Target file: {target}")
        print(f"Total test inputs: {len(all_inputs)}")

        print("\nSource (branch points marked with >):")
        for lineno, line in enumerate(source.splitlines(), 1):
            print(f"{'>' if lineno in points else ' '} {lineno:3d} | {line}")

        print(f"\nStatic branch points: {sorted(points)}")
        print(f"Possible branch outcomes ({len(possible)}): {fmt(possible)}")

        observations = measure_all(fn, all_inputs, target)

        print("\nBranch coverage for the first 10 tests:")
        for observation in observations[:10]:
            print(
                f"  #{observation.index:<4d} {fmt(observation.arcs)} "
                f"result={observation.result!r} input={all_inputs[observation.index]}"
            )

        covered = union_arcs(observations)
        distinct = {observation.arcs for observation in observations}

        print(f"\nUnion of branch outcomes over {len(observations)} tests: {fmt(covered)}")
        print(f"Covered branch outcomes: {len(covered)} / {len(possible)}")
        print(f"Unreached branch outcomes: {fmt(possible - covered)}")
        print(f"Distinct per-test branch sets: {len(distinct)}")
        for arcs in sorted(distinct, key=sorted):
            count = sum(1 for o in observations if o.arcs == arcs)
            print(f"  {fmt(arcs)} -> {count} tests")

        discarded = set()
        for observation in observations:
            discarded.update(observation.discarded_arcs)
        print(f"Arcs discarded as non-branch outcomes (e.g. exception exits): {fmt(discarded)}")

        raw = max(observations, key=lambda o: len(o.lines))
        print(
            "\nSequential-arc check (test #%d): coverage.py recorded arcs for lines %s; "
            "only origins in %s became adequacy items -> %s"
            % (raw.index, sorted(raw.lines), sorted(points), fmt(raw.arcs))
        )

        errors = [o for o in observations if o.error]
        empty = [o for o in observations if not o.arcs]
        foreign = sorted(
            {f for o in observations for f in o.measured_files} - {str(target.resolve())}
        )

        print("\nValidation:")
        checks = [
            ("no execution errors", not errors, f"{len(errors)} failing tests"),
            (
                "every test reaches at least one branch outcome",
                not empty,
                f"{len(empty)} tests with no branch item",
            ),
            ("only the target file measured", not foreign, f"foreign files: {foreign}"),
            (
                "covered outcomes are statically possible branch arcs",
                covered <= possible,
                f"unexpected arcs: {fmt(covered - possible)}",
            ),
            (
                "no adequacy item originates outside a branch point",
                all(a in points for o in observations for a, _ in o.arcs),
                "sequential arc leaked into the adequacy items",
            ),
            (
                "no arc discarded as an impossible outcome",
                not discarded,
                f"discarded: {fmt(discarded)}",
            ),
            ("tests differ in covered branches", len(distinct) > 1, "all tests identical"),
        ]
        ok = True
        for name, passed, detail in checks:
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + ("" if passed else f" -- {detail}"))
            ok = ok and passed

        for observation in errors[:5]:
            print(f"  error on test #{observation.index}: {observation.error}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
