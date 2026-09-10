"""Validate per-test statement coverage on HumanEval/0.

Infrastructure validation only: the canonical implementation stands in for a
program under test. In the real reproduction, adequacy is measured against each
faulty implementation (assumption A1, see docs/experiment_plan.md).

Runs every HumanEval+ input (base + plus) against the target under its own
coverage session and reports the executed line set per test.
"""

import sys
import tempfile
from pathlib import Path

from evalplus.data import get_human_eval_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from table_iv_replication.statement_coverage import (  # noqa: E402
    executable_lines,
    load_entry_point,
    measure_all,
    measure_one,
    union_lines,
    write_target,
)

TASK_ID = "HumanEval/0"


def main() -> int:
    problem = get_human_eval_plus()[TASK_ID]
    entry_point = problem["entry_point"]
    source = problem["prompt"] + problem["canonical_solution"]
    all_inputs = problem["base_input"] + problem["plus_input"]

    with tempfile.TemporaryDirectory(prefix="table_iv_") as tmpdir:
        target = write_target(source, Path(tmpdir) / "humaneval_0_target.py")
        fn = load_entry_point(target, entry_point)
        statements = executable_lines(target)

        print(f"Task: {TASK_ID}")
        print(f"Entry point: {entry_point}")
        print(f"Target file: {target}")
        print(f"Base inputs: {len(problem['base_input'])}")
        print(f"Plus inputs: {len(problem['plus_input'])}")
        print(f"Total test inputs: {len(all_inputs)}")

        print("\nSource (executable statements marked with *):")
        for lineno, line in enumerate(source.splitlines(), 1):
            mark = "*" if lineno in statements else " "
            print(f"{mark} {lineno:3d} | {line}")
        print(f"\nExecutable statements: {sorted(statements)}")

        observations = measure_all(fn, all_inputs, target)

        print("\nStatement coverage for the first 10 tests:")
        for observation in observations[:10]:
            args = all_inputs[observation.index]
            print(
                f"  #{observation.index:<4d} lines={sorted(observation.lines)} "
                f"result={observation.result!r} input={args}"
            )

        covered = union_lines(observations)
        distinct = {observation.lines for observation in observations}
        module_level = statements - covered

        print(f"\nUnion of statement coverage over {len(observations)} tests: "
              f"{sorted(covered)}")
        print(f"Covered executable lines: {len(covered)} / {len(statements)}")
        print(f"Never covered by any single test: {sorted(module_level)} "
              "(module-level lines, executed once at import time)")
        print(f"Distinct per-test coverage sets: {len(distinct)}")
        for lines in sorted(distinct, key=sorted):
            count = sum(1 for o in observations if o.lines == lines)
            print(f"  {sorted(lines)} -> {count} tests")

        unfiltered = measure_one(fn, all_inputs[0], target, restrict_to_target=False)
        print("\nDiagnostic - same test measured WITHOUT the include filter:")
        for path in sorted(unfiltered.measured_files):
            print(f"  {path}")

        errors = [o for o in observations if o.error]
        empty = [o for o in observations if not o.lines]
        foreign = sorted(
            {f for o in observations for f in o.measured_files}
            - {str(target.resolve())}
        )

        print("\nValidation:")
        checks = [
            ("no execution errors", not errors, f"{len(errors)} failing tests"),
            ("no empty coverage sets", not empty, f"{len(empty)} empty sets"),
            (
                "only the target file measured",
                not foreign,
                f"foreign files: {foreign}",
            ),
            (
                "covered lines are executable statements",
                covered <= statements,
                f"unexpected lines: {sorted(covered - statements)}",
            ),
            (
                "tests differ in covered lines",
                len(distinct) > 1,
                "every test covered the same lines",
            ),
        ]
        ok = True
        for name, passed, detail in checks:
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}"
                  + ("" if passed else f" -- {detail}"))
            ok = ok and passed

        for observation in errors[:5]:
            print(f"  error on test #{observation.index}: {observation.error}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
