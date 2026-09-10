"""Provenance audit of the PromptAnalysis artifacts. Read-only, no execution.

Answers: what is actually in these files, how do they line up with EvalPlus
HumanEval+, and what could they supply for a Table IV reproduction. It does not
run generated code, classify faults, or select anything.
"""

import sys
from collections import Counter
from pathlib import Path

from evalplus.data import get_human_eval_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from table_iv_replication.promptanalysis_adapter import (  # noqa: E402
    BENCHMARK_DATASET,
    ORIGINAL_GENERATIONS,
    US_DATASET,
    US_GENERATIONS,
    load_generations,
    load_prompt_variants,
    manifest,
)

GENERATION_METADATA_KEYS = [
    "model", "model_name", "engine", "temperature", "top_p", "seed",
    "timestamp", "created", "created_at", "n", "num_samples", "sample_index",
    "generation_id", "run_id", "finish_reason", "usage",
]


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def normalize(text: str | None) -> str | None:
    """Harmless formatting only: trailing whitespace per line, leading/trailing newlines."""
    if text is None:
        return None
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def drop_blank_lines(text: str) -> str:
    return "\n".join(line for line in normalize(text).splitlines() if line)


def compare_texts(left: str | None, right: str | None) -> str:
    """Tiered verdict. Only whitespace is ever normalized away, never wording."""
    if left == right:
        return "exact"
    if left is None or right is None:
        return "missing"
    if normalize(left) == normalize(right):
        return "whitespace-only"
    if drop_blank_lines(left) == drop_blank_lines(right):
        return "blank-lines-only"
    return "mismatch"


VERDICTS = ["exact", "whitespace-only", "blank-lines-only", "mismatch", "missing"]


def signature_line(prompt: str, entry_point: str) -> str | None:
    for line in prompt.splitlines():
        if line.lstrip().startswith(f"def {entry_point}("):
            return line.strip()
    return None


def audit_provenance() -> None:
    rule("PROVENANCE")
    record = manifest()
    print(f"Repo:   {record['repo']}")
    print(f"Commit: {record['commit']}")
    print(f"Source: {record['source']}")
    print(f"Note:   {record['note']}")
    print("\nArtifacts:")
    for name, entry in sorted(record["artifacts"].items()):
        print(f"  {name}")
        print(f"    size   {entry['size']} bytes")
        print(f"    sha256 {entry['sha256']}")
        print(f"    url    {entry['url']}")
        print(f"    at     {entry['downloaded_at']}")


def audit_us_dataset(problems):
    rule(f"TASK 3 -- {US_DATASET}")
    variants = load_prompt_variants(US_DATASET)
    malformed = [v for v in variants if "__malformed__" in v.raw]
    ids = [v.task_id for v in variants]
    duplicates = {tid: n for tid, n in Counter(ids).items() if n > 1}

    print(f"Records: {len(variants)}")
    print(f"Malformed records: {len(malformed)}")
    print(f"Unique task ids: {len(set(ids))}")
    print(f"Duplicate task ids: {duplicates or 'none'}")
    print(f"applicable=True:  {sum(1 for v in variants if v.applicable is True)}")
    print(f"applicable=False: {sum(1 for v in variants if v.applicable is False)}")
    print(f"applicable missing/other: {sum(1 for v in variants if v.applicable not in (True, False))}")
    not_applicable = [v.task_id for v in variants if v.applicable is not True]
    print(f"applicable=False task ids: {not_applicable}")
    print("  (these carry no mutated_prompt: no under-specification could be applied)")
    print(f"mutation_type values: {dict(Counter(v.mutation_type for v in variants))}")
    print(f"Fields present: {sorted(variants[0].raw)}")
    print(f"Missing entry_point:        {sum(1 for v in variants if not v.entry_point)}"
          "  <- field absent from this schema")
    print(f"Missing canonical_solution: {sum(1 for v in variants if not v.canonical_solution)}"
          "  <- field absent from this schema")
    print(f"Missing original_prompt: {sum(1 for v in variants if not v.original_prompt)}")
    print(f"Missing mutated_prompt:  {sum(1 for v in variants if not v.mutated_prompt)}")

    if malformed:
        problems.append(f"{US_DATASET}: {len(malformed)} malformed records")
    return variants


def audit_against_evalplus(variants, problems):
    rule("TASK 3 -- prompt correspondence with EvalPlus HumanEval+")
    plus = get_human_eval_plus()
    print(f"EvalPlus HumanEval+ tasks: {len(plus)} (measured, not assumed)")
    print(f"PromptAnalysis US tasks:   {len(variants)}")

    us_ids, plus_ids = {v.task_id for v in variants}, set(plus)
    print(f"Task ids identical: {us_ids == plus_ids}")
    print(f"Only in PromptAnalysis: {sorted(us_ids - plus_ids) or 'none'}")
    print(f"Only in EvalPlus:       {sorted(plus_ids - us_ids) or 'none'}")

    verdicts = Counter()
    mismatches = []
    for variant in variants:
        problem = plus.get(variant.task_id)
        if problem is None:
            verdicts["no-evalplus-task"] += 1
            continue
        verdict = compare_texts(variant.original_prompt, problem["prompt"])
        verdicts[verdict] += 1
        if verdict == "mismatch":
            mismatches.append((variant.task_id, variant.original_prompt, problem["prompt"]))

    print("\noriginal_prompt vs EvalPlus prompt:")
    print(f"  exact matches:                       {verdicts['exact']}")
    print(f"  formatting-only (trailing ws / newlines): {verdicts['whitespace-only']}")
    print(f"  formatting-only (blank lines):       {verdicts['blank-lines-only']}")
    print(f"  semantic/text mismatches:            {verdicts['mismatch']}")
    for task_id, left, right in mismatches[:3]:
        print(f"\n  --- {task_id} ---")
        left_lines, right_lines = left.splitlines(), right.splitlines()
        for i in range(max(len(left_lines), len(right_lines))):
            a = left_lines[i] if i < len(left_lines) else "<absent>"
            b = right_lines[i] if i < len(right_lines) else "<absent>"
            if a != b:
                print(f"    L{i + 1} PromptAnalysis: {a!r}")
                print(f"    L{i + 1} EvalPlus:       {b!r}")
    if len(mismatches) > 3:
        print(f"  ... and {len(mismatches) - 3} more mismatching tasks")

    rule("TASK 3 -- do the under-specified prompts keep the original signature?")
    prefixes = Counter()
    signature_kept = signature_absent = entry_point_absent = skipped = 0
    changed = []
    for variant in variants:
        problem = plus.get(variant.task_id)
        if problem is None or not variant.mutated_prompt:
            skipped += 1
            continue
        entry_point = problem["entry_point"]
        mutated = variant.mutated_prompt
        first = mutated.splitlines()[0] if mutated.splitlines() else ""
        prefixes[first if not first.startswith(("from ", "import ", "def ")) else "<code>"] += 1
        if f"def {entry_point}(" not in mutated:
            entry_point_absent += 1
            continue
        original_signature = signature_line(problem["prompt"], entry_point)
        mutated_signature = signature_line(mutated, entry_point)
        if mutated_signature is None:
            signature_absent += 1
        elif mutated_signature == original_signature:
            signature_kept += 1
        else:
            changed.append((variant.task_id, original_signature, mutated_signature))

    total = len(variants)
    checked = total - skipped
    print(f"Skipped (applicable=False, no mutated_prompt): {skipped}")
    print(f"Checked: {checked}/{total}")
    print(f"Entry point 'def <name>(' present in mutated_prompt: {checked - entry_point_absent}/{checked}")
    print(f"Signature line byte-identical to EvalPlus:           {signature_kept}/{checked}")
    print(f"Signature changed: {len(changed)}   signature unreadable: {signature_absent}")
    for task_id, before, after in changed[:5]:
        print(f"  {task_id}: {before!r} -> {after!r}")
    print(f"First line of mutated_prompt: {dict(prefixes)}")
    if entry_point_absent:
        problems.append(f"{US_DATASET}: {entry_point_absent} mutated prompts lost the entry point")


def audit_generations(name, plus, problems, *, expect_us=False, us_variants=None):
    rule(f"TASK {'5' if expect_us else '4'} -- {name}")
    records = load_generations(name)
    ids = [r.task_id for r in records]
    counts = Counter(ids)
    duplicates = {tid: n for tid, n in counts.items() if n > 1}

    print(f"Records: {len(records)}")
    print(f"Unique task ids: {len(counts)}")
    print(f"Records per task: {sorted(set(counts.values()))}  "
          f"(min {min(counts.values())}, max {max(counts.values())})")
    print(f"Duplicate task ids: {duplicates or 'none'}")
    print(f"=> {'ONE generation per task' if set(counts.values()) == {1} else 'MULTIPLE generations per task'}")
    print(f"\nFields present: {sorted(records[0].metadata)}")

    empty = [r.task_id for r in records if not (r.generated_code or "").strip()]
    print(f"\nGeneratedCode present: {sum(1 for r in records if r.generated_code is not None)}/{len(records)}")
    print(f"GeneratedCode empty/blank: {len(empty)} {empty}")
    print(f"GeneratedResponse present: {sum(1 for r in records if r.generated_response is not None)}/{len(records)}")
    print(f"Eval_Status values: {dict(Counter(r.eval_status for r in records))}")
    print(f"Pass@1 values: {dict(Counter(r.metadata.get('Pass@1') for r in records))}")
    passed = sum(1 for r in records if r.metadata.get("Pass@1") is True)
    print(f"Pass@1 True: {passed}/{len(records)} ({passed / len(records):.1%})")

    print("\nGeneration metadata:")
    for key in GENERATION_METADATA_KEYS:
        present = [r for r in records if key in r.metadata]
        print(f"  {key:15s} {'present in ' + str(len(present)) + ' records' if present else 'NOT AVAILABLE IN THIS ARTIFACT'}")

    # Do the embedded tests come from the benchmark, or from a generator?
    rule(f"TASK 6 -- TestCases provenance in {name}")
    benchmark = {v.task_id: v for v in load_prompt_variants(BENCHMARK_DATASET)}
    same_as_test = sum(1 for r in records if r.metadata.get("TestCases") == r.metadata.get("test"))
    same_as_bench = sum(
        1 for r in records
        if r.task_id in benchmark
        and r.metadata.get("TestCases") == benchmark[r.task_id].raw.get("test")
    )
    same_as_plus = sum(
        1 for r in records
        if r.task_id in plus and r.metadata.get("TestCases") == plus[r.task_id]["test"]
    )
    print(f"TestCases identical to the record's own 'test' field:     {same_as_test}/{len(records)}")
    print(f"TestCases identical to {BENCHMARK_DATASET} 'test':  {same_as_bench}/{len(records)}")
    print(f"TestCases identical to the EvalPlus 'test' field:         {same_as_plus}/{len(records)}")
    print(f"n_Tests distribution (top 8): {dict(Counter(r.metadata.get('n_Tests') for r in records).most_common(8))}")
    print("Reading: TestCases carrying the stock HumanEval check() assertions (same")
    print("         'author': 'jt' METADATA header) are BENCHMARK tests, not an")
    print("         LLM-generated (LLM-Plain) pool.")

    print("\nReference-implementation provenance:")
    cs_bench = sum(
        1 for r in records
        if r.task_id in benchmark
        and r.variant.canonical_solution == benchmark[r.task_id].canonical_solution
    )
    cs_plus = sum(
        1 for r in records
        if r.task_id in plus and r.variant.canonical_solution == plus[r.task_id]["canonical_solution"]
    )
    print(f"  canonical_solution == {BENCHMARK_DATASET}: {cs_bench}/{len(records)}")
    print(f"  canonical_solution == EvalPlus canonical_solution:  {cs_plus}/{len(records)}")
    print("  (EvalPlus rewrites HumanEval canonical solutions; the two are different")
    print("   reference implementations of the same task.)")
    sample = next(r for r in records if r.metadata.get("TestCases"))
    print(f"\nTestCases sample ({sample.task_id}), first 6 lines:")
    for line in (sample.metadata["TestCases"] or "").splitlines()[:8]:
        print(f"    {line}")

    if expect_us:
        rule(f"TASK 5 -- US correspondence checks for {name}")
        print(f"mutation_type values: {dict(Counter(r.variant.mutation_type for r in records))}")
        print(f"applicable values:    {dict(Counter(r.variant.applicable for r in records))}")

        by_id = {v.task_id: v for v in us_variants}
        print(f"Task ids align with {US_DATASET}: "
              f"{ {r.task_id for r in records} == set(by_id) }")

        verdicts = Counter()
        for record in records:
            verdicts[f"PromptUsed vs own mutated_prompt: {compare_texts(record.prompt_used, record.variant.mutated_prompt)}"] += 1
            verdicts[f"PromptUsed vs own original_prompt: {compare_texts(record.prompt_used, record.variant.original_prompt)}"] += 1
            dataset_variant = by_id.get(record.task_id)
            if dataset_variant:
                verdicts[f"mutated_prompt vs {US_DATASET}: {compare_texts(record.variant.mutated_prompt, dataset_variant.mutated_prompt)}"] += 1
                stripped = (dataset_variant.mutated_prompt or "").removeprefix("TASK:\n")
                verdicts[f"mutated_prompt vs dataset minus 'TASK:' prefix: {compare_texts(record.variant.mutated_prompt, stripped)}"] += 1
                verdicts[f"original_prompt vs {US_DATASET}: {compare_texts(record.variant.original_prompt, dataset_variant.original_prompt)}"] += 1
            if record.task_id in plus:
                verdicts[f"entry_point vs EvalPlus: {'same' if record.variant.entry_point == plus[record.task_id]['entry_point'] else 'DIFFERENT'}"] += 1
                verdicts[f"canonical_solution vs EvalPlus: {compare_texts(record.variant.canonical_solution, plus[record.task_id]['canonical_solution'])}"] += 1
                verdicts[f"original_prompt vs EvalPlus prompt: {compare_texts(record.variant.original_prompt, plus[record.task_id]['prompt'])}"] += 1
        for key in sorted(verdicts):
            print(f"  {key}: {verdicts[key]}")

    return records


def main() -> int:
    problems: list[str] = []
    audit_provenance()
    us_variants = audit_us_dataset(problems)
    audit_against_evalplus(us_variants, problems)

    plus = get_human_eval_plus()
    original = audit_generations(ORIGINAL_GENERATIONS, plus, problems)
    us = audit_generations(US_GENERATIONS, plus, problems, expect_us=True, us_variants=us_variants)

    rule("TASK 6 -- reuse potential for Table IV")
    per_task_original = set(Counter(r.task_id for r in original).values())
    per_task_us = set(Counter(r.task_id for r in us).values())
    rows = [
        ("US prompt dataset", "Possibly", "Candidate prompt source; correspondence verified above",
         "Not proven to be the Table IV prompts; adds a 'TASK:' prefix the generation artifact drops"),
        ("gpt-5-mini original generations", "Partially",
         f"{len(original)} records, {sorted(per_task_original)} per task",
         "Paper needs 10 generations/task at temperature 0.8; this has 1 and stores no sampling parameters"),
        ("gpt-5-mini US generations", "Partially",
         f"{len(us)} records, {sorted(per_task_us)} per task",
         "Same multiplicity gap; provenance vs Table IV unproven"),
        ("Embedded TestCases", "No (as a test pool)", "HumanEval benchmark check() assertions",
         "These are benchmark tests, not an LLM-generated pool; LLM-Plain tests still missing"),
        ("EvalPlus HumanEval+", "Yes", "Ground truth already validated in this repo",
         "Supplies expected behaviour, not the paper's test pool"),
    ]
    width = max(len(r[0]) for r in rows)
    for name, useful, fidelity, limitation in rows:
        print(f"\n{name:<{width}} | useful: {useful}")
        print(f"{'':<{width}} | fidelity:   {fidelity}")
        print(f"{'':<{width}} | limitation: {limitation}")

    rule("SUMMARY")
    print(f"Generations available per task: original {sorted(per_task_original)}, US {sorted(per_task_us)}")
    print("Target paper requires: 10 generations per prompt variant per model at temperature 0.8.")
    print(f"=> These artifacts supply {sorted(per_task_original | per_task_us)} generation(s) per task.")
    if problems:
        print("\nProblems found:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("\nNo structural problems found in the artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
