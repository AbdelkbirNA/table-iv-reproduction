"""Public single-generation pilot: which public GPT-5-mini programs are faulty?

NOT a Table IV reproduction. The PromptAnalysis artifacts hold ONE generation
per task per prompt variant; the target paper used ten at temperature 0.8. Every
number here is a "public single-generation pilot" figure.

Primary oracle: EvalPlus HumanEval+ canonical implementation (decision A4).
Secondary sensitivity oracle: the original HumanEval canonical solution.

    python scripts/classify_public_gpt5mini_faults.py [--limit N] [--tasks IDS]
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from evalplus.data import get_human_eval_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from table_iv_replication.fault_classifier import (  # noqa: E402
    CORRECT,
    DIFFICULTY_THRESHOLD,
    FAULTY,
    INCONCLUSIVE,
    PROMPT_ORIGINAL,
    PROMPT_US,
    SENS_CORRECT_BOTH,
    SENS_EVALPLUS_ONLY,
    SENS_FAULTY_BOTH,
    SENS_INCONCLUSIVE,
    SENS_ORIGINAL_ONLY,
    UNUSABLE,
    Candidate,
    classify_task,
    summarize,
)
from table_iv_replication.promptanalysis_adapter import (  # noqa: E402
    BENCHMARK_DATASET,
    ORIGINAL_GENERATIONS,
    US_GENERATIONS,
    load_generations,
    load_prompt_variants,
)

DETAIL_OUT = ROOT / "results" / "public_gpt5mini_fault_classification.json"
SUMMARY_OUT = ROOT / "results" / "public_gpt5mini_fault_summary.json"

BANNER = (
    "PUBLIC SINGLE-GENERATION PILOT -- NOT A TABLE IV REPRODUCTION\n"
    "1 generation per task per prompt variant; the paper used 10 at temperature 0.8."
)


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def group_stats(results, label):
    summary = summarize(results)
    summary["group"] = label
    return summary


def print_group(label, summary):
    print(f"\n{label}")
    print(f"  total records                     {summary['total_records']}")
    print(f"  GeneratedCode present             {summary['generated_code_present']}")
    print(f"  executable (loaded + entry point) {summary['executable_records']}")
    print(f"  unusable                          {summary['unusable']}")
    print(f"  FAULTY_PRIMARY                    {summary['faulty_primary']}")
    print(f"  CORRECT_ON_OBSERVED_DOMAIN        {summary['correct_on_observed_domain']}")
    print(f"  INCONCLUSIVE                      {summary['inconclusive']}")
    print(f"  provisional difficult (>= {DIFFICULTY_THRESHOLD})   "
          f"{summary['provisional_difficult_candidates']}")
    difficulty = summary["difficulty"]
    if difficulty:
        print(f"  difficulty among faulty: mean {difficulty['mean']:.4f}  "
              f"median {difficulty['median']:.4f}  "
              f"min {difficulty['min']:.4f}  max {difficulty['max']:.4f}")
    else:
        print("  difficulty among faulty: n/a (no faulty candidates)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="classify only the first N tasks")
    parser.add_argument("--tasks", help="comma-separated task ids")
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--timeout-budget", type=int, default=5)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    print(BANNER)

    plus = get_human_eval_plus()
    benchmark = {v.task_id: v for v in load_prompt_variants(BENCHMARK_DATASET)}
    generations = {
        PROMPT_ORIGINAL: (
            ORIGINAL_GENERATIONS,
            {g.task_id: g for g in load_generations(ORIGINAL_GENERATIONS)},
        ),
        PROMPT_US: (
            US_GENERATIONS,
            {g.task_id: g for g in load_generations(US_GENERATIONS)},
        ),
    }

    task_ids = sorted(plus, key=lambda t: int(t.split("/")[1]))
    if args.tasks:
        task_ids = [t.strip() for t in args.tasks.split(",")]
    if args.limit:
        task_ids = task_ids[: args.limit]

    print(f"\nPrimary oracle:   EvalPlus HumanEval+ canonical implementation (A4)")
    print(f"Secondary oracle: original HumanEval canonical solution "
          f"({BENCHMARK_DATASET})")
    print(f"Test domain:      EvalPlus base_input + plus_input")
    print(f"Tasks:            {len(task_ids)}")
    print(f"Per-input timeout {args.timeout}s, budget {args.timeout_budget} timeouts/program\n")

    started = time.monotonic()
    results = []
    inputs_executed = 0

    for position, task_id in enumerate(task_ids, 1):
        problem = plus[task_id]
        inputs = problem["base_input"] + problem["plus_input"]
        original_variant = benchmark.get(task_id)

        candidates = []
        for variant, (artifact, records) in generations.items():
            record = records.get(task_id)
            if record is None:
                continue
            candidates.append(
                Candidate(
                    candidate_id=f"{task_id}|{variant}",
                    task_id=task_id,
                    prompt_variant=variant,
                    artifact=artifact,
                    source=record.generated_code,
                )
            )
        if not candidates:
            continue

        task_results = classify_task(
            task_id,
            problem["entry_point"],
            inputs,
            candidates,
            evalplus_reference=problem["prompt"] + problem["canonical_solution"],
            original_reference=(
                original_variant.original_prompt + original_variant.canonical_solution
                if original_variant and original_variant.canonical_solution
                else None
            ),
            atol=problem["atol"],
            timeout=args.timeout,
            timeout_budget=args.timeout_budget,
        )
        results.extend(task_results)
        inputs_executed += len(inputs) * (2 + len([c for c in candidates if c.has_code]))

        marker = {FAULTY: "F", CORRECT: ".", INCONCLUSIVE: "?", UNUSABLE: "x"}
        print("".join(marker[r.primary_classification] for r in task_results),
              end="", flush=True)
        if position % 30 == 0:
            print(f"  {position}/{len(task_ids)}")
    print()

    elapsed = time.monotonic() - started

    by_variant = {
        PROMPT_ORIGINAL: [r for r in results if r.prompt_variant == PROMPT_ORIGINAL],
        PROMPT_US: [r for r in results if r.prompt_variant == PROMPT_US],
    }
    summaries = {
        "original_prompt": group_stats(by_variant[PROMPT_ORIGINAL], "original_prompt"),
        "under_specified_prompt": group_stats(by_variant[PROMPT_US], "under_specified_prompt"),
        "combined": group_stats(results, "combined"),
    }

    rule("CLASSIFICATION")
    print(f"Runtime: {elapsed:.1f}s   candidate/reference executions: ~{inputs_executed}")
    for label, key in [
        ("A. ORIGINAL PROMPT", "original_prompt"),
        ("B. UNDER-SPECIFIED PROMPT", "under_specified_prompt"),
        ("C. COMBINED", "combined"),
    ]:
        print_group(label, summaries[key])

    rule("REFERENCE SENSITIVITY (EvalPlus vs original HumanEval)")
    sensitivity = Counter(r.sensitivity for r in results)
    for key, label in [
        (SENS_FAULTY_BOTH, "faulty under both references"),
        (SENS_CORRECT_BOTH, "correct under both references"),
        (SENS_EVALPLUS_ONLY, "faulty only under EvalPlus"),
        (SENS_ORIGINAL_ONLY, "faulty only under original HumanEval"),
        (SENS_INCONCLUSIVE, "sensitivity inconclusive"),
    ]:
        print(f"  {label:<42} {sensitivity[key]}")
    flipped = [
        r for r in results
        if r.sensitivity in (SENS_EVALPLUS_ONLY, SENS_ORIGINAL_ONLY)
    ]
    print(f"\nClassifications that change with the reference: {len(flipped)}")
    for record in flipped:
        print(f"  {record.candidate_id:<40} {record.sensitivity}  "
              f"(primary {record.triggered_inputs}/{record.observed_inputs}, "
              f"secondary {record.secondary_triggered_inputs}/{record.secondary_observed_inputs})")
    affected_tasks = sorted({r.task_id for r in flipped}, key=lambda t: int(t.split("/")[1]))
    print(f"Task ids affected: {affected_tasks or 'none'}")

    rule("TASK-LEVEL OVERLAP")
    by_task = {}
    for record in results:
        by_task.setdefault(record.task_id, {})[record.prompt_variant] = record
    overlap = Counter()
    overlap_tasks: dict[str, list[str]] = {}
    for task_id, variants in by_task.items():
        original_faulty = variants.get(PROMPT_ORIGINAL) and variants[PROMPT_ORIGINAL].primary_classification == FAULTY
        us_faulty = variants.get(PROMPT_US) and variants[PROMPT_US].primary_classification == FAULTY
        if original_faulty and us_faulty:
            key = "both_faulty"
        elif original_faulty:
            key = "original_faulty_only"
        elif us_faulty:
            key = "us_faulty_only"
        else:
            key = "neither_faulty"
        overlap[key] += 1
        overlap_tasks.setdefault(key, []).append(task_id)
    for key in ("both_faulty", "original_faulty_only", "us_faulty_only", "neither_faulty"):
        print(f"  {key:<22} {overlap[key]}")

    rule(f"TOP 15 MOST DIFFICULT FAULTY CANDIDATES (evalplus_domain_difficulty)")
    faulty = [r for r in results if r.primary_classification == FAULTY]
    faulty.sort(key=lambda r: (-(r.evalplus_domain_difficulty or 0), r.task_id))
    print(f"{'task_id':<16}{'variant':<18}{'difficulty':>11}  triggered/observed")
    for record in faulty[:15]:
        print(f"{record.task_id:<16}{record.prompt_variant:<18}"
              f"{record.evalplus_domain_difficulty:>11.6f}  "
              f"{record.triggered_inputs}/{record.observed_inputs}")

    if not args.no_save:
        DETAIL_OUT.parent.mkdir(parents=True, exist_ok=True)
        DETAIL_OUT.write_text(
            json.dumps(
                {
                    "label": "public single-generation pilot -- NOT Table IV reproduction",
                    "primary_oracle": "EvalPlus 0.3.1 HumanEval+ canonical implementation (decision A4)",
                    "secondary_oracle": f"original HumanEval canonical solution ({BENCHMARK_DATASET})",
                    "comparator": "table_iv_replication._output_oracle.compare_outputs (mirrors evalplus 0.3.1)",
                    "test_domain": "EvalPlus base_input + plus_input",
                    "difficulty_definition": (
                        "evalplus_domain_difficulty = 1 - triggered/observed over the EvalPlus "
                        "input domain. NOT the paper's fault difficulty, which is measured over "
                        "an augmented suite including LLM-generated differential tests."
                    ),
                    "difficulty_threshold": DIFFICULTY_THRESHOLD,
                    "generations_per_task": 1,
                    "paper_generations_per_task": 10,
                    "runtime_seconds": round(elapsed, 2),
                    "candidates": [r.as_dict() for r in results],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        SUMMARY_OUT.write_text(
            json.dumps(
                {
                    "label": "public single-generation pilot -- NOT Table IV reproduction",
                    "runtime_seconds": round(elapsed, 2),
                    "tasks": len(by_task),
                    "groups": summaries,
                    "reference_sensitivity": dict(sensitivity),
                    "reference_sensitivity_flipped_candidates": [r.candidate_id for r in flipped],
                    "reference_sensitivity_affected_tasks": affected_tasks,
                    "task_overlap": dict(overlap),
                    "task_overlap_ids": overlap_tasks,
                    "top_difficult": [
                        {
                            "task_id": r.task_id,
                            "prompt_variant": r.prompt_variant,
                            "evalplus_domain_difficulty": r.evalplus_domain_difficulty,
                            "triggered_inputs": r.triggered_inputs,
                            "observed_inputs": r.observed_inputs,
                        }
                        for r in faulty[:15]
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nSaved: {DETAIL_OUT.relative_to(ROOT)}")
        print(f"Saved: {SUMMARY_OUT.relative_to(ROOT)}")

    print(f"\n{BANNER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
