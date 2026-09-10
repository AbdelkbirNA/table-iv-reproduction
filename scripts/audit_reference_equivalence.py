"""Are HumanEval's two reference implementations behaviourally equivalent?

The original HumanEval canonical solution (shipped verbatim by PromptAnalysis)
and EvalPlus's rewritten one differ in source for most tasks. Fault
classification compares generated code against *a* reference, so this audit
establishes whether the choice can change a trigger label on the HumanEval+
input domain.

Read-only with respect to the artifacts; both references execute in child
processes with per-input timeouts.

    python scripts/audit_reference_equivalence.py [--limit N] [--tasks ID,ID] [--no-reverse]
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from evalplus.data import get_human_eval_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from table_iv_replication.promptanalysis_adapter import (  # noqa: E402
    BENCHMARK_DATASET,
    load_prompt_variants,
)
from table_iv_replication.reference_oracle import (  # noqa: E402
    BEHAVIOR_MISMATCH,
    EQUIVALENT_ON_DOMAIN,
    INCONCLUSIVE,
    SOURCE_IDENTICAL,
    compare_references,
)

OUTPUT = ROOT / "results" / "reference_equivalence.json"
CONSOLE_MISMATCH_TASKS = 5
CONSOLE_MISMATCH_INPUTS = 3


def build(prompt: str, canonical_solution: str) -> str:
    return prompt + canonical_solution


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="audit only the first N tasks")
    parser.add_argument("--tasks", help="comma-separated task ids to audit")
    parser.add_argument(
        "--no-reverse",
        action="store_true",
        help="skip the swapped-argument pass that probes oracle asymmetry",
    )
    parser.add_argument("--timeout", type=float, default=1.0,
                        help="per-input wall-clock limit for each reference")
    parser.add_argument("--timeout-budget", type=int, default=5,
                        help="timed-out inputs after which a task is abandoned")
    args = parser.parse_args(argv)

    plus = get_human_eval_plus()
    original = {v.task_id: v for v in load_prompt_variants(BENCHMARK_DATASET)}

    task_ids = sorted(plus, key=lambda t: int(t.split("/")[1]))
    if args.tasks:
        task_ids = [t.strip() for t in args.tasks.split(",")]
    if args.limit:
        task_ids = task_ids[: args.limit]

    print(f"Reference A (candidate): PromptAnalysis {BENCHMARK_DATASET} "
          "== original HumanEval canonical solution")
    print("Reference B (expected):  EvalPlus HumanEval+ canonical solution")
    print("Comparator: evalplus 0.3.1 unsafe_execute per-input oracle "
          "(mirrored; reuses evalplus is_floats and _poly)")
    print(f"Tasks to audit: {len(task_ids)}")

    started = time.monotonic()
    verdicts = []
    total_inputs = 0
    missing = []

    for position, task_id in enumerate(task_ids, 1):
        problem = plus[task_id]
        variant = original.get(task_id)
        if variant is None or not variant.canonical_solution:
            missing.append(task_id)
            continue

        inputs = problem["base_input"] + problem["plus_input"]
        verdict = compare_references(
            task_id,
            build(variant.original_prompt, variant.canonical_solution),
            build(problem["prompt"], problem["canonical_solution"]),
            problem["entry_point"],
            inputs,
            atol=problem["atol"],
            timeout=args.timeout,
            timeout_budget=args.timeout_budget,
        )
        total_inputs += verdict.inputs_tested
        record = verdict.as_dict()
        record["inputs_available"] = len(inputs)
        record["entry_point"] = problem["entry_point"]
        record["atol"] = problem["atol"]

        if not args.no_reverse and verdict.classification != SOURCE_IDENTICAL:
            # The EvalPlus oracle is asymmetric (is_floats(exp), type(out), the
            # find_zero special case), so run it the other way round too.
            reverse = compare_references(
                task_id,
                build(problem["prompt"], problem["canonical_solution"]),
                build(variant.original_prompt, variant.canonical_solution),
                problem["entry_point"],
                inputs,
                atol=problem["atol"],
                timeout=args.timeout,
                timeout_budget=args.timeout_budget,
            )
            record["reverse_classification"] = reverse.classification
            record["reverse_agrees_with_forward"] = (
                reverse.classification == verdict.classification
            )
            total_inputs += reverse.inputs_tested

        verdicts.append((verdict, record))
        marker = {
            SOURCE_IDENTICAL: ".",
            EQUIVALENT_ON_DOMAIN: "=",
            BEHAVIOR_MISMATCH: "X",
            INCONCLUSIVE: "?",
        }[verdict.classification]
        print(marker, end="", flush=True)
        if position % 60 == 0:
            print(f"  {position}/{len(task_ids)}")
    print()

    elapsed = time.monotonic() - started
    counts = Counter(v.classification for v, _ in verdicts)
    mismatched = [v for v, _ in verdicts if v.classification == BEHAVIOR_MISMATCH]
    inconclusive = [v for v, _ in verdicts if v.classification == INCONCLUSIVE]
    asymmetric = [
        record["task_id"]
        for _, record in verdicts
        if record.get("reverse_agrees_with_forward") is False
    ]

    print(f"\n{'=' * 78}\nRESULT\n{'=' * 78}")
    print(f"Tasks audited:        {len(verdicts)}")
    print(f"Per-input timeout:    {args.timeout}s, budget {args.timeout_budget} timeouts/task")
    print(f"Tasks skipped:        {len(missing)} {missing}")
    print(f"Test inputs executed: {total_inputs} (per implementation, both directions)")
    print(f"Runtime:              {elapsed:.1f}s\n")
    print(f"  {SOURCE_IDENTICAL:<52} {counts[SOURCE_IDENTICAL]}")
    print(f"  {EQUIVALENT_ON_DOMAIN:<52} {counts[EQUIVALENT_ON_DOMAIN]}")
    print(f"  {BEHAVIOR_MISMATCH:<52} {counts[BEHAVIOR_MISMATCH]}")
    print(f"  {INCONCLUSIVE:<52} {counts[INCONCLUSIVE]}")
    print(f"\nForward/reverse classification disagreements: {asymmetric or 'none'}")

    kinds = Counter()
    unobserved_kinds = Counter()
    for verdict, _ in verdicts:
        kinds.update(m.kind for m in verdict.mismatches)
        unobserved_kinds.update(u.kind for u in verdict.unobserved)
    print(f"Disagreeing inputs by kind:  {dict(kinds) or 'none'}")
    print(f"Unobserved inputs by kind:   {dict(unobserved_kinds) or 'none'}")

    if mismatched:
        print(f"\nBehaviour mismatches ({len(mismatched)} tasks, first "
              f"{CONSOLE_MISMATCH_TASKS} shown):")
        for verdict in mismatched[:CONSOLE_MISMATCH_TASKS]:
            print(f"\n  {verdict.task_id}  ({verdict.note})")
            for item in verdict.mismatches[:CONSOLE_MISMATCH_INPUTS]:
                print(f"    input #{item.index}: {item.args}")
                print(f"      original HumanEval : {item.left}")
                print(f"      EvalPlus           : {item.right}")
                print(f"      reason             : {item.reason}")
            if len(verdict.mismatches) > CONSOLE_MISMATCH_INPUTS:
                print(f"    ... {len(verdict.mismatches) - CONSOLE_MISMATCH_INPUTS} more inputs")
    if inconclusive:
        print(f"\nInconclusive ({len(inconclusive)} tasks) -- agreed wherever observed,")
        print("but some inputs could not be observed on both sides:")
        for verdict in inconclusive:
            counts = Counter(u.kind for u in verdict.unobserved)
            print(f"  {verdict.task_id}: compared {verdict.inputs_tested}, "
                  f"unobserved {len(verdict.unobserved)} {dict(counts)}")
            example = next((u for u in verdict.unobserved if u.kind == "timeout"), None)
            if example:
                print(f"    e.g. input #{example.index} {example.args}: {example.reason}")

    print(f"\nMismatching task ids:   {[v.task_id for v in mismatched] or 'none'}")
    print(f"Inconclusive task ids:  {[v.task_id for v in inconclusive] or 'none'}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "comparator": (
                    "evalplus 0.3.1 unsafe_execute per-input oracle, mirrored in "
                    "table_iv_replication._output_oracle.compare_outputs; reuses "
                    "evalplus.eval.is_floats and evalplus.eval._special_oracle._poly"
                ),
                "reference_a": f"PromptAnalysis {BENCHMARK_DATASET} (original HumanEval canonical_solution)",
                "reference_b": "EvalPlus HumanEval+ canonical_solution",
                "test_domain": "EvalPlus base_input + plus_input",
                "tasks_audited": len(verdicts),
                "tasks_skipped": missing,
                "inputs_executed": total_inputs,
                "runtime_seconds": round(elapsed, 2),
                "counts": dict(counts),
                "per_input_timeout_seconds": args.timeout,
                "timeout_budget_per_task": args.timeout_budget,
                "disagreeing_inputs_by_kind": dict(kinds),
                "unobserved_inputs_by_kind": dict(unobserved_kinds),
                "forward_reverse_disagreements": asymmetric,
                "limitation": (
                    "Agreement on this domain is observational equivalence over the "
                    "available inputs, NOT proof of program equivalence."
                ),
                "tasks": [record for _, record in verdicts],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nFull machine-readable result: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
