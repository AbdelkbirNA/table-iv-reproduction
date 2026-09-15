"""First real LLM-generated-test pilot: three faults, three requests, no repair.

RECONSTRUCTION PILOT -- NOT a Table IV reproduction. Three public
single-generation GPT-5-mini candidates get one LLM-Plain-style test-generation
request each. No repair call of any kind is made, so the untouched first
generation can be inspected.

    python scripts/run_llm_plain_pilot.py [--force] [--dry-run]
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

from evalplus.data import get_human_eval_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from table_iv_replication.generated_test_runner import (  # noqa: E402
    DetectionVerdict,
    TestStatus,
    run_generated_tests,
)
from table_iv_replication.llm_plain_protocol import (  # noqa: E402
    PUBLIC_YATE_FAITHFUL,
    GeneratedTestSuite,
    build_python_generation_prompt,
    build_python_system_prompt,
)
from table_iv_replication.openai_generation import (  # noqa: E402
    API_KEY_NOT_CONFIGURED,
    GenerationCall,
    api_key_configured,
    generate,
)
from table_iv_replication.promptanalysis_adapter import (  # noqa: E402
    ORIGINAL_GENERATIONS,
    US_GENERATIONS,
    load_generations,
)

MODEL = "gpt-5-mini-2025-08-07"
TEMPERATURE = 0.1
OUT = ROOT / "results" / "llm_plain_pilot"
RAW = OUT / "raw"
CLASSIFICATION = ROOT / "results" / "public_gpt5mini_fault_classification.json"

#: Exactly these three, fixed. No substitution is permitted.
PILOT = (
    ("HumanEval/91", "under_specified"),
    ("HumanEval/78", "under_specified"),
    ("HumanEval/151", "original"),
)

ARTIFACT_FOR = {
    "original": ORIGINAL_GENERATIONS,
    "under_specified": US_GENERATIONS,
}

#: Ways a generated test can reach the program under test. Reported, never rewritten.
ACCESS_PATTERNS = {
    "direct_call": "calls the entry point with no import",
    "import_solution": "from solution import ... / import solution",
    "import_implementation": "from implementation import ...",
    "import_module_under_test": "from module_under_test import ...",
    "import_other": "imports some other module name",
    "redefines_target": "redefines the function inside the test file",
    "none": "never references the entry point",
}


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def display(path: Path) -> str:
    """Project-relative path when it is one, absolute otherwise."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def classify_access(code: str, entry_point: str) -> tuple[str, list[str]]:
    """How does this generated module try to reach the target? Report only."""
    imports = [
        line.strip()
        for line in code.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    joined = "\n".join(imports)
    if f"def {entry_point}(" in code:
        pattern = "redefines_target"
    elif "solution" in joined:
        pattern = "import_solution"
    elif "implementation" in joined:
        pattern = "import_implementation"
    elif "module_under_test" in joined:
        pattern = "import_module_under_test"
    elif entry_point in joined:
        pattern = "import_other"
    elif f"{entry_point}(" in code:
        pattern = "direct_call"
    else:
        pattern = "none"
    return pattern, imports


def load_candidates():
    """Exact faulty sources from the PromptAnalysis artifacts; nothing retyped."""
    generations = {
        variant: {g.task_id: g for g in load_generations(artifact)}
        for variant, artifact in ARTIFACT_FOR.items()
    }
    classification = {
        (c["task_id"], c["prompt_variant"]): c
        for c in json.loads(CLASSIFICATION.read_text(encoding="utf-8"))["candidates"]
    }
    plus = get_human_eval_plus()

    candidates = []
    for task_id, variant in PILOT:
        record = generations[variant].get(task_id)
        label = json.dumps([task_id, variant])
        if record is None or not (record.generated_code or "").strip():
            raise SystemExit(f"STOP: {label} has no usable GeneratedCode")
        saved = classification.get((task_id, variant))
        if saved is None:
            raise SystemExit(f"STOP: {label} is absent from {CLASSIFICATION.name}")
        if saved["primary_classification"] != "FAULTY_PRIMARY":
            raise SystemExit(
                f"STOP: {label} is {saved['primary_classification']}, not FAULTY_PRIMARY"
            )
        problem = plus[task_id]
        candidates.append(
            {
                "candidate_id": f"{task_id}|{variant}",
                "task_id": task_id,
                "prompt_variant": variant,
                "artifact": ARTIFACT_FOR[variant],
                "entry_point": problem["entry_point"],
                "faulty_source": record.generated_code,
                "faulty_sha256": hashlib.sha256(
                    record.generated_code.encode("utf-8")
                ).hexdigest(),
                "reference_source": problem["prompt"] + problem["canonical_solution"],
                "atol": problem["atol"],
                "evalplus_domain_difficulty": saved["evalplus_domain_difficulty"],
                "triggered_inputs": saved["triggered_inputs"],
                "observed_inputs": saved["observed_inputs"],
            }
        )
    return candidates


def raw_path(candidate) -> Path:
    return RAW / f"{candidate['task_id'].replace('/', '_')}__{candidate['prompt_variant']}.json"


def write_record(path: Path, record: dict) -> None:
    """Persist a record atomically.

    Written to a sibling temporary file and renamed into place, so an interrupt
    mid-write can never leave a half-written JSON file that a later run would
    mistake for a usable cached response. ``Path.replace`` is atomic on POSIX.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_cached_record(candidate) -> dict | None:
    """Return a saved record **only** if it holds a usable API response.

    The API call is the sole part of this pilot that costs money, so it is the
    part that gets cached. Parsing and execution are free and are always redone,
    which is also what makes an interrupted run resumable: a run killed during
    execution still has its response on disk and completes on the next attempt
    without spending anything.

    A record whose call failed is *not* a cache hit -- retrying a failure is the
    point of rerunning.
    """
    path = raw_path(candidate)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    api = record.get("api")
    if not isinstance(api, dict) or api.get("response_text") is None:
        return None
    return record


def quarantine(path: Path) -> Path:
    """Move an unusable raw file aside instead of destroying it.

    Reached only when a file exists but cannot be read back as a successful
    response. It is never silently overwritten: a failed call's recorded error
    is audit material, and a corrupt file is evidence of something worth seeing.
    """
    backup = path.with_name(f"{path.stem}.unusable-{int(time.time())}{path.suffix}")
    path.rename(backup)
    return backup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing successful raw response")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve candidates and build prompts, make no API call")
    args = parser.parse_args(argv)

    print("PILOT PLAN")
    print("3 requests maximum")
    print(f"model = {MODEL}")
    print(f"temperature = {TEMPERATURE}")
    print("repairs = disabled")
    print("\nRECONSTRUCTION PILOT -- NOT a Table IV reproduction.")
    print(f"Model provenance: INFERRED_ADAPTATION (the paper does not name the "
          f"LLM-Plain test-generation model).")
    print("Temperature provenance: CONFIRMED_PUBLIC_YATE_IMPLEMENTATION / "
          "UNKNOWN_TARGET_TABLE_IV_CONFIGURATION.")

    candidates = load_candidates()

    rule("CANDIDATES")
    print(f"{'candidate':<34}{'entry point':<22}{'difficulty':>11}  triggering inputs")
    for candidate in candidates:
        print(
            f"{candidate['candidate_id']:<34}{candidate['entry_point']:<22}"
            f"{candidate['evalplus_domain_difficulty']:>11.6f}  "
            f"{candidate['triggered_inputs']}/{candidate['observed_inputs']}"
        )
        print(f"{'':<34}faulty source sha256 {candidate['faulty_sha256']}")

    # Resolve the cache before anything else, so the exact number of paid
    # requests is known and printed before a single one is made.
    plan = [
        (candidate, None if args.force else load_cached_record(candidate))
        for candidate in candidates
    ]
    to_generate = [candidate for candidate, cached in plan if cached is None]
    reusing = [candidate for candidate, cached in plan if cached is not None]

    rule("CACHE PLAN")
    for candidate, cached in plan:
        path = raw_path(candidate)
        if cached is not None:
            state = f"REUSE existing response ({path.name})"
        elif args.force and path.exists():
            state = "REGENERATE (--force, existing response will be replaced)"
        elif path.exists():
            state = "REGENERATE (existing file is unusable; it will be kept aside)"
        else:
            state = "GENERATE (no response on disk)"
        print(f"{candidate['candidate_id']:<34}{state}")
    print(f"\nPaid API requests this run: {len(to_generate)}  "
          f"(reusing {len(reusing)} cached)")

    if args.dry_run:
        rule("DRY RUN")
        for candidate in candidates:
            prompt = build_python_generation_prompt(
                candidate["faulty_source"], PUBLIC_YATE_FAITHFUL
            )
            print(f"{candidate['candidate_id']}: prompt {len(prompt)} chars")
        print("\nNo API call was made (--dry-run).")
        return 0

    # The key is needed only if something actually has to be generated. A fully
    # cached rerun completes offline.
    if to_generate and not api_key_configured():
        rule("RESULT")
        print(API_KEY_NOT_CONFIGURED)
        print(f"\nNo API call was made. {len(to_generate)} candidate(s) still need "
              "generating:")
        for candidate in to_generate:
            print(f"  {candidate['candidate_id']}")
        print("Set OPENAI_API_KEY in the environment and re-run; everything else in")
        print("this pilot is already verified.")
        return 2

    RAW.mkdir(parents=True, exist_ok=True)
    system_prompt = build_python_system_prompt(PUBLIC_YATE_FAITHFUL)
    records = []

    rule(f"GENERATION -- {len(to_generate)} REQUEST(S), NO REPAIR")
    for candidate, cached in plan:
        user_prompt = build_python_generation_prompt(
            candidate["faulty_source"], PUBLIC_YATE_FAITHFUL
        )

        if cached is not None:
            call = GenerationCall.from_dict(cached["api"])
            record = dict(cached)
            record["reused_from_cache"] = True
            print(f"{candidate['candidate_id']:<34} reused (no API call)")
            print(f"{'':<34} model returned: {call.model_returned}  "
                  f"requested at: {call.requested_at}")
            records.append((candidate, call, record))
            continue

        path = raw_path(candidate)
        if path.exists():
            kept = quarantine(path)
            print(f"{candidate['candidate_id']:<34} kept unusable file as {kept.name}")

        call = generate(
            model=MODEL,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=TEMPERATURE,
        )
        record = {
            "pilot_label": "RECONSTRUCTION PILOT -- not a Table IV reproduction",
            "candidate_id": candidate["candidate_id"],
            "task_id": candidate["task_id"],
            "prompt_variant": candidate["prompt_variant"],
            "source_artifact": candidate["artifact"],
            "entry_point": candidate["entry_point"],
            "faulty_source_sha256": candidate["faulty_sha256"],
            "evalplus_domain_difficulty": candidate["evalplus_domain_difficulty"],
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "config": PUBLIC_YATE_FAITHFUL.to_dict(),
            "repair_requests_made": 0,
            "reused_from_cache": False,
            "api": call.to_dict(),
        }
        # Save immediately, before parsing or execution can fail: this response
        # is the only thing that cost money.
        write_record(path, record)
        status = "ok" if call.succeeded else f"FAILED: {call.error}"
        print(f"{candidate['candidate_id']:<34} {status}")
        print(f"{'':<34} model returned: {call.model_returned}  "
              f"finish: {call.finish_reason}  http attempts: {call.http_requests_made}")
        if call.api_rejected_parameters:
            print(f"{'':<34} API REJECTED: {list(call.api_rejected_parameters)}; "
                  f"temperature actually sent: {call.temperature_sent}")
        for error in call.api_errors:
            print(f"{'':<34} api error recorded: {error[:160]}")
        records.append((candidate, call, record))

    rule("PARSING (no repair, no manual editing)")
    suites = {}
    for candidate, call, record in records:
        if not call.succeeded:
            print(f"{candidate['candidate_id']}: no response to parse")
            continue
        suite = GeneratedTestSuite.from_response(
            suite_id=candidate["candidate_id"],
            task_id=candidate["task_id"],
            fault_id=candidate["candidate_id"],
            config=PUBLIC_YATE_FAITHFUL,
            system_prompt=record["system_prompt"],
            generation_prompt=record["user_prompt"],
            raw_response=call.response_text,
        )
        suites[candidate["candidate_id"]] = suite
        pattern, imports = classify_access(
            suite.extracted_code or "", candidate["entry_point"]
        )
        record["parser"] = {
            "raw_response_chars": len(call.response_text),
            "extracted_code_chars": len(suite.extracted_code or ""),
            "parse_succeeded": suite.syntax_error is None and bool(suite.test_cases),
            "syntax_error": suite.syntax_error,
            "test_count": len(suite.test_cases),
            "test_names": [case.name for case in suite.test_cases],
            "has_module_level_asserts": any(c.module_level for c in suite.test_cases),
            "imports": imports,
            "access_pattern": pattern,
            "helper_functions": [
                line.split("(")[0].replace("def ", "").strip()
                for line in (suite.extracted_code or "").splitlines()
                if line.startswith("def ") and not line.startswith("def test")
            ],
        }
        parser = record["parser"]
        print(f"\n{candidate['candidate_id']}")
        print(f"  raw response       {parser['raw_response_chars']} chars")
        print(f"  extracted code     {parser['extracted_code_chars']} chars")
        print(f"  parse succeeded    {parser['parse_succeeded']}  "
              f"syntax_error={parser['syntax_error']}")
        print(f"  tests extracted    {parser['test_count']}  {parser['test_names']}")
        print(f"  module-level asserts {parser['has_module_level_asserts']}")
        print(f"  helper functions   {parser['helper_functions']}")
        print(f"  imports            {parser['imports']}")
        print(f"  access pattern     {parser['access_pattern']} "
              f"-- {ACCESS_PATTERNS[parser['access_pattern']]}")
        write_record(raw_path(candidate), record)

    rule("EXECUTION AGAINST REFERENCE AND FAULTY (isolated, no repair)")
    for candidate, call, record in records:
        suite = suites.get(candidate["candidate_id"])
        if suite is None or not suite.test_cases:
            print(f"{candidate['candidate_id']}: nothing executable")
            continue
        results = run_generated_tests(
            candidate["reference_source"],
            candidate["faulty_source"],
            candidate["entry_point"],
            suite.test_cases,
            fault_id=candidate["candidate_id"],
            atol=candidate["atol"],
            timeout=2.0,
        )
        verdicts = Counter(r.verdict.value for r in results)
        metrics = {
            "total_parsed_tests": len(results),
            "executed_successfully": sum(
                1 for r in results
                if r.reference_run.status in (TestStatus.PASS, TestStatus.ASSERTION_FAILURE)
            ),
            "invalid_tests": sum(
                1 for r in results
                if r.reference_run.status in (TestStatus.INVALID_TEST, TestStatus.RUNTIME_ERROR,
                                              TestStatus.TIMEOUT)
            ),
            "reference_pass": sum(1 for r in results if r.reference_run.status is TestStatus.PASS),
            "faulty_pass": sum(1 for r in results if r.faulty_run.status is TestStatus.PASS),
            "triggering": sum(1 for r in results if r.triggers_fault),
            "detecting": sum(1 for r in results if r.detects_fault),
            "faulty_biased_oracle": verdicts.get(DetectionVerdict.FAULTY_BIASED_ORACLE.value, 0),
            "invalid_oracle": verdicts.get(DetectionVerdict.INVALID_ORACLE.value, 0),
            "no_target_call": sum(1 for r in results if r.invocation_count == 0),
            "suite_triggers_fault": any(r.triggers_fault for r in results),
            "suite_detects_fault": any(r.detects_fault for r in results),
            "verdicts": dict(verdicts),
        }
        record["execution"] = {
            "metrics": metrics,
            "tests": [r.to_dict() for r in results],
        }
        write_record(raw_path(candidate), record)

        print(f"\n{candidate['candidate_id']}")
        print(f"  {'test':<34}{'reference':<20}{'faulty':<20}{'trig':<7}{'det':<6}verdict")
        for result in results:
            print(f"  {result.test_name:<34}{result.reference_run.status.value:<20}"
                  f"{result.faulty_run.status.value:<20}"
                  f"{str(result.triggered):<7}{str(result.detects_fault):<6}"
                  f"{result.verdict.value}")
        print(f"  parsed={metrics['total_parsed_tests']} "
              f"executed={metrics['executed_successfully']} invalid={metrics['invalid_tests']} "
              f"triggering={metrics['triggering']} detecting={metrics['detecting']}")
        print(f"  faulty_biased_oracle={metrics['faulty_biased_oracle']} "
              f"invalid_oracle={metrics['invalid_oracle']} "
              f"no_target_call={metrics['no_target_call']}")
        print(f"  suite_triggers_fault={metrics['suite_triggers_fault']}  "
              f"suite_detects_fault={metrics['suite_detects_fault']}")
        print("  (raw pilot booleans -- NOT Table IV FTR/FDR)")

    rule("API USAGE")
    totals = Counter()
    spent_now = Counter()
    for candidate, call, record in records:
        usage = call.usage
        reused = record.get("reused_from_cache", False)
        print(f"{candidate['candidate_id']:<34}{'(reused) ' if reused else '':<10}"
              f"{json.dumps(usage)}")
        for key, value in usage.items():
            if isinstance(value, int):
                totals[key] += value
                if not reused:
                    spent_now[key] += value
    print(f"\ncumulative across all candidates: {json.dumps(dict(totals))}")
    print(f"billed by THIS run (cached excluded): {json.dumps(dict(spent_now))}")
    print("No dollar cost is estimated: no authoritative price is configured.")

    manifest = {
        "pilot_label": "RECONSTRUCTION PILOT -- not a Table IV reproduction",
        "model_requested": MODEL,
        "model_provenance": "INFERRED_ADAPTATION",
        "temperature_requested": TEMPERATURE,
        "temperature_provenance": (
            "CONFIRMED_PUBLIC_YATE_IMPLEMENTATION / UNKNOWN_TARGET_TABLE_IV_CONFIGURATION"
        ),
        "generation_requests": len(records),
        "api_requests_billed_this_run": sum(
            1 for _, _, r in records if not r.get("reused_from_cache", False)
        ),
        "reused_from_cache": sum(
            1 for _, _, r in records if r.get("reused_from_cache", False)
        ),
        "repair_requests": 0,
        "repairs_enabled": False,
        "total_usage": dict(totals),
        "candidates": [
            {
                "candidate_id": candidate["candidate_id"],
                "raw_file": raw_path(candidate).name,
                "reused_from_cache": record.get("reused_from_cache", False),
                "faulty_source_sha256": candidate["faulty_sha256"],
                "evalplus_domain_difficulty": candidate["evalplus_domain_difficulty"],
                "model_returned": call.model_returned,
                "http_requests_made": call.http_requests_made,
                "api_rejected_parameters": list(call.api_rejected_parameters),
                "parser": record.get("parser"),
                "metrics": record.get("execution", {}).get("metrics"),
            }
            for candidate, call, record in records
        ],
    }
    write_record(OUT / "manifest.json", manifest)
    print(f"\nSaved: {display(OUT / 'manifest.json')}")
    print(f"Saved: {display(RAW)}/ ({len(records)} raw responses)")
    print("\nRepair requests made: 0 (repairs disabled for this phase)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
