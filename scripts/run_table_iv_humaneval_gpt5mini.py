"""Table IV protocol, run end to end on real GPT-5-mini HumanEval faults.

This produces **real measured numbers**, but it is a reconstruction, not the
paper's experiment. Two substitutions dominate everything below and are printed
with every result:

A8  The paper's two LLM-generated suites -- ``fault_discovery_augmented_tests``
    (which defines the corpus and difficulty) and ``table_iv_llm_plain_tests``
    (the pool ``TS_f`` suites are sampled from) -- are not public. We split the
    EvalPlus HumanEval+ input domain disjointly and deterministically to keep
    the two roles apart, then build ``TS_f`` from the pool half by oracle-blind
    coverage-greedy selection at the paper's density (~9.4 tests per fault).
    The selector reads coverage only, never trigger status, so a triggering
    input enters the pool only incidentally -- as it would with a generator
    that cannot see the reference solution.

A9  Our tests carry the reference implementation's output as their expectation.
    That oracle is correct, so it flags every fault its input triggers and FDR
    equals FTR by construction. It is an **upper bound** on the paper's FDR,
    which uses oracles an LLM wrote while looking at the faulty program. The
    two FDR columns must never be compared at face value.

Because the pool size drives the absolute FTR (a coverage-adequate suite keeps
only a few tests, so its chance of retaining a rare triggering one falls as the
pool grows), the run also sweeps pool size rather than reporting one number as
if it were pool-independent.

    python scripts/run_table_iv_humaneval_gpt5mini.py [--limit N] [--tasks IDS]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from evalplus.data import get_human_eval_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from table_iv_replication import branch_coverage, statement_coverage  # noqa: E402
from table_iv_replication.domain_pool import (  # noqa: E402
    REFERENCE_DIFFERENTIAL,
    build_observations,
    domain_difficulty,
    select_coverage_pool,
    split_domain,
    sweep_pool_sizes,
)
from table_iv_replication.fault_classifier import DIFFICULTY_THRESHOLD  # noqa: E402
from table_iv_replication.metrics import aggregate_rates  # noqa: E402
from table_iv_replication.mutation_coverage import measure_mutation_source  # noqa: E402
from table_iv_replication.promptanalysis_adapter import (  # noqa: E402
    ORIGINAL_GENERATIONS,
    US_GENERATIONS,
    load_generations,
)
from table_iv_replication.reference_oracle import compare_references  # noqa: E402
from table_iv_replication.sampling import derive_seed, run_repetitions  # noqa: E402

OUT = ROOT / "results" / "table_iv_humaneval_gpt5mini.json"
CRITERIA = ("statement", "branch", "mutation")

BANNER = (
    "RECONSTRUCTION -- NOT THE PAPER'S EXPERIMENT\n"
    "A8 pool substituted: oracle-blind coverage-greedy suite from a real input\n"
    "   domain, at the paper's density -- not table_iv_llm_plain_tests\n"
    "A9 oracle substituted (reference-differential) -- FDR here is an UPPER BOUND"
)


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def collect_faults(task_ids, plus, generations, *, seed, timeout, timeout_budget):
    """Classify candidates on the discovery half and keep the difficult ones."""
    faults = []
    skipped = []
    for task_id in task_ids:
        problem = plus[task_id]
        inputs = problem["base_input"] + problem["plus_input"]
        reference = problem["prompt"] + problem["canonical_solution"]
        discovery_idx, pool_idx = split_domain(
            len(inputs), seed=derive_seed(seed, f"split|{task_id}")
        )

        for variant, (artifact, records) in generations.items():
            record = records.get(task_id)
            if record is None or not record.generated_code:
                continue
            candidate_id = f"{task_id}|{variant}"

            verdict = compare_references(
                task_id,
                record.generated_code,
                reference,
                problem["entry_point"],
                inputs,
                atol=problem["atol"],
                timeout=timeout,
                timeout_budget=timeout_budget,
                max_mismatches=len(inputs),
            )
            triggering = {v.index for v in verdict.mismatches}
            unobserved = {v.index for v in verdict.unobserved}

            discovery_obs = [i for i in discovery_idx if i not in unobserved]
            pool_obs = [i for i in pool_idx if i not in unobserved]
            difficulty = domain_difficulty(
                sum(1 for i in discovery_obs if i in triggering), len(discovery_obs)
            )

            # Fault existence is decided on the held-out discovery half, the role
            # `fault_discovery_augmented_tests` plays in the paper. Difficulty is
            # NOT filtered here: the paper measures it over a ~9-test suite, and
            # measuring it over a 500-input fuzzing half-domain instead selects a
            # far more extreme population (difficulty ~0.999, i.e. 1-2 triggering
            # inputs in 1000). That is the "rule exact, denominator wrong" row of
            # docs/protocol_gap_matrix.md. Difficulty is applied later, on TS_f,
            # where the paper's definition actually lives.
            discovery_triggered = sum(1 for i in discovery_obs if i in triggering)
            reason = None
            if not triggering:
                reason = "not faulty on the observed domain"
            elif difficulty is None:
                reason = "no observable input in the discovery half"
            elif not discovery_triggered:
                reason = "faulty, but not discovered by the held-out discovery half"
            elif not pool_obs:
                reason = "no observable input in the pool half"
            if reason:
                skipped.append({"candidate_id": candidate_id, "reason": reason})
                continue

            faults.append(
                {
                    "candidate_id": candidate_id,
                    "task_id": task_id,
                    "prompt_variant": variant,
                    "source_artifact": artifact,
                    "source": record.generated_code,
                    "entry_point": problem["entry_point"],
                    "domain_size": len(inputs),
                    "discovery_observed": len(discovery_obs),
                    "discovery_triggered": discovery_triggered,
                    "discovery_half_difficulty": difficulty,
                    "pool_indices": pool_obs,
                    "pool_triggering": sorted(i for i in pool_obs if i in triggering),
                    "inputs": [inputs[i] for i in pool_obs],
                }
            )
            print("F", end="", flush=True)
        print(".", end="", flush=True)
    print()
    return faults, skipped


def build_pool_and_adequacy(fault, *, target_size, seed, timeout):
    """Select TS_f oracle-blind, then measure all three criteria on it.

    Coverage is measured over the whole pool half first, because the selection
    needs it; mutation runs only on the selected tests, which is both the
    cheaper order and the correct one -- mutation adequacy is a property of the
    pool, not of the domain it was drawn from.
    """
    source, entry = fault["source"], fault["entry_point"]
    ids = [f"input#{i}" for i in fault["pool_indices"]]
    diagnostics = {"domain_half_size": len(ids)}

    statements, _ = statement_coverage.measure_source(source, entry, fault["inputs"])
    statement_items = {ids[o.index]: frozenset(f"L{n}" for n in o.lines) for o in statements}
    branches, _ = branch_coverage.measure_source(source, entry, fault["inputs"])
    branch_items = {ids[o.index]: frozenset(f"{a[0]}->{a[1]}" for a in o.arcs) for o in branches}

    # Oracle-blind: the selector sees coverage only, never the trigger status.
    structural = {
        test_id: statement_items.get(test_id, frozenset())
        | branch_items.get(test_id, frozenset())
        for test_id in statement_items
    }
    selected = select_coverage_pool(structural, target_size=target_size, seed=seed)
    position = {test_id: index for index, test_id in enumerate(ids)}
    selected_inputs = [fault["inputs"][position[t]] for t in selected]

    per_criterion = {
        "statement": {t: statement_items.get(t, frozenset()) for t in selected},
        "branch": {t: branch_items.get(t, frozenset()) for t in selected},
    }
    run = measure_mutation_source(source, entry, selected_inputs, timeout=timeout)
    per_criterion["mutation"] = {
        selected[o.index]: frozenset(o.killed) for o in run.observations
    }
    diagnostics.update(
        pool_size=len(selected),
        mutants=len(run.mutants),
        mutants_killed=len(run.killed),
        baseline_mismatches=len(run.baseline_mismatches),
        mutation_failures=len(run.failures),
        mutation_seconds=round(run.elapsed, 2),
    )
    return selected, per_criterion, diagnostics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument("--pool-size", type=int, default=10,
                        help="target |TS_f|; the paper averages ~9.4 tests per fault")
    parser.add_argument("--limit", type=int, help="only the first N tasks")
    parser.add_argument("--tasks", help="comma-separated task ids")
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--timeout-budget", type=int, default=5)
    parser.add_argument("--sweep-sizes", default="3,5,10,20,40")
    parser.add_argument("--sweep-draws", type=int, default=20)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    print(BANNER)
    started = time.monotonic()

    plus = get_human_eval_plus()
    generations = {
        "original": (ORIGINAL_GENERATIONS, {g.task_id: g for g in load_generations(ORIGINAL_GENERATIONS)}),
        "under_specified": (US_GENERATIONS, {g.task_id: g for g in load_generations(US_GENERATIONS)}),
    }
    task_ids = sorted(plus, key=lambda t: int(t.split("/")[1]))
    if args.tasks:
        task_ids = [t.strip() for t in args.tasks.split(",")]
    if args.limit:
        task_ids = task_ids[: args.limit]

    rule("STEP 1 -- FAULT CORPUS (discovery half)")
    print(f"tasks: {len(task_ids)}   seed: {args.seed}   retention: difficulty >= {DIFFICULTY_THRESHOLD}")
    faults, skipped = collect_faults(
        task_ids, plus, generations,
        seed=args.seed, timeout=args.timeout, timeout_budget=args.timeout_budget,
    )
    print(f"\nretained faults: {len(faults)}   skipped candidates: {len(skipped)}")
    if not faults:
        print("No fault retained; nothing to sample.")
        return 1

    rule(f"STEP 2 -- ORACLE-BLIND POOL TS_f (target {args.pool_size} tests) AND ADEQUACY")
    print("The paper reports 4,872 LLM-Plain tests over 520 HumanEval faults (~9.4/fault).")
    print(f"\n{'fault':<34}{'half':>6}{'pool':>6}{'trig in pool':>14}{'mutants':>9}{'killed':>8}")
    observations = {c: {} for c in CRITERIA}
    diagnostics = {}
    pool_has_trigger = {}
    for fault in faults:
        selected, per_criterion, diag = build_pool_and_adequacy(
            fault,
            target_size=args.pool_size,
            seed=derive_seed(args.seed, f"pool|{fault['candidate_id']}"),
            timeout=args.timeout,
        )
        diagnostics[fault["candidate_id"]] = diag
        triggering_ids = {f"input#{i}" for i in fault["pool_triggering"]}
        in_pool = triggering_ids.intersection(selected)
        pool_has_trigger[fault["candidate_id"]] = bool(in_pool)
        diag["triggering_in_pool"] = len(in_pool)
        diag["pool_difficulty"] = (
            1.0 - len(in_pool) / len(selected) if selected else None
        )
        for criterion in CRITERIA:
            adequacy = per_criterion[criterion]
            observations[criterion][fault["candidate_id"]] = build_observations(
                adequacy, triggering_ids.intersection(adequacy),
                oracle=REFERENCE_DIFFERENTIAL,
            )
        print(f"{fault['candidate_id']:<34}{diag['domain_half_size']:>6}{len(selected):>6}"
              f"{len(in_pool):>14}{diag['mutants']:>9}{diag['mutants_killed']:>8}", flush=True)

    exposed = [f for f, hit in pool_has_trigger.items() if hit]
    print(f"\npools containing at least one triggering test: {len(exposed)}/{len(faults)}")

    rule(f"STEP 3 -- SAMPLING ({args.repetitions} iterations per fault and criterion)")

    def aggregate(subset):
        """FTR/FDR per criterion over `subset` of the fault corpus."""
        table = {}
        for criterion in CRITERIA:
            suites_by_fault = {
                fault_id: run_repetitions(
                    observations[criterion][fault_id],
                    repetitions=args.repetitions,
                    seed=derive_seed(args.seed, fault_id),
                )
                for fault_id in subset
            }
            if not suites_by_fault:
                continue
            rates = [
                aggregate_rates({f: s[i] for f, s in suites_by_fault.items()})
                for i in range(args.repetitions)
            ]
            sizes = [
                sum(len(s[i]) for s in suites_by_fault.values()) / len(suites_by_fault)
                for i in range(args.repetitions)
            ]
            table[criterion] = {
                "faults": len(suites_by_fault),
                "ftr": sum(r[0] for r in rates) / len(rates),
                "fdr_upper_bound": sum(r[1] for r in rates) / len(rates),
                "mean_suite_size": sum(sizes) / len(sizes),
            }
        return table

    per_fault_rates = {c: {} for c in CRITERIA}
    for criterion in CRITERIA:
        for fault_id, obs in observations[criterion].items():
            suites = run_repetitions(
                obs, repetitions=args.repetitions, seed=derive_seed(args.seed, fault_id)
            )
            per_fault_rates[criterion][fault_id] = {
                "ftr": sum(any(t.triggers_fault for t in s) for s in suites) / len(suites),
                "mean_suite_size": sum(len(s) for s in suites) / len(suites),
                "pool_size": len(obs),
                "triggering_in_pool": sum(1 for t in obs if t.triggers_fault),
            }

    paper_rule = [
        f for f in exposed
        if (diagnostics[f]["pool_difficulty"] or 0.0) >= DIFFICULTY_THRESHOLD
    ]
    headline = aggregate(list(observations["statement"]))
    conditional = aggregate(exposed)
    paper_regime = aggregate(paper_rule)

    def show(title, table, note):
        print(f"\n{title}")
        print(f"  {note}")
        print(f"  {'criterion':<12}{'faults':>8}{'FTR':>10}{'FDR (upper bd)':>18}{'suite size':>13}")
        for criterion in CRITERIA:
            row = table.get(criterion)
            if not row:
                continue
            print(f"  {criterion:<12}{row['faults']:>8}{row['ftr']:>10.4f}"
                  f"{row['fdr_upper_bound']:>18.4f}{row['mean_suite_size']:>13.4f}")

    show("A. WHOLE RETAINED CORPUS", headline,
         "Folds in whether an oracle-blind pool happened to contain a trigger at all.")
    show("B. FAULTS WHOSE POOL CONTAINS A TRIGGER", conditional,
         "Isolates the question Table IV asks: does adequate sampling RETAIN the trigger?")
    show(f"C. PAPER'S RETENTION RULE ON TS_f (difficulty >= {DIFFICULTY_THRESHOLD})", paper_regime,
         "Difficulty measured where the paper defines it: over the sampling suite itself.")

    rule("STEP 4 -- POOL-SIZE SENSITIVITY")
    print("The paper's pool averages ~9.4 LLM-Plain tests per fault (4872/520).")
    print(f"{'pool size':>12}{'mean pool':>12}{'statement FTR':>16}{'branch FTR':>13}{'mutation FTR':>15}")
    sizes = [int(s) for s in args.sweep_sizes.split(",") if s.strip()]
    sweep = {
        criterion: sweep_pool_sizes(
            observations[criterion], sizes,
            draws=args.sweep_draws, repetitions=args.repetitions,
            seed=derive_seed(args.seed, f"sweep|{criterion}"),
        )
        for criterion in CRITERIA
    }
    by_size = {}
    for criterion in CRITERIA:
        for row in sweep[criterion]:
            by_size.setdefault(row["requested_pool_size"], {})[criterion] = row
    for size in sorted(by_size):
        row = by_size[size]
        if len(row) < len(CRITERIA):
            continue
        print(f"{size:>12}{row['statement']['mean_pool_size']:>12.1f}"
              f"{row['statement']['ftr']:>16.4f}{row['branch']['ftr']:>13.4f}"
              f"{row['mutation']['ftr']:>15.4f}")

    elapsed = time.monotonic() - started
    payload = {
        "label": "Table IV reconstruction on real GPT-5-mini HumanEval faults",
        "not_a_faithful_reproduction": True,
        "benchmark": "HumanEval+ (EvalPlus 0.3.1)",
        "fault_model": "gpt-5-mini (PromptAnalysis public generations, 1 per prompt; paper used 10 @ T=0.8)",
        "assumptions": {
            "A8_pool": (
                "table_iv_llm_plain_tests is not public. The EvalPlus input domain is split "
                "disjointly and deterministically into a discovery half (fault corpus and "
                "difficulty) and a pool half (the sampling pool TS_f)."
            ),
            "A9_oracle": (
                "Tests carry the reference implementation's output as their expectation. Such an "
                "oracle is correct, so detection equals triggering and the FDR reported here is "
                "an UPPER BOUND on the paper's FDR, not a reproduction of it."
            ),
            "A2_mutation_engine": "mutmut 3.7.0; the paper does not name its Python mutation tool.",
        },
        "seed": args.seed,
        "repetitions": args.repetitions,
        "difficulty_threshold": DIFFICULTY_THRESHOLD,
        "runtime_seconds": round(elapsed, 2),
        "faults_retained": len(faults),
        "candidates_skipped": len(skipped),
        "pool_target_size": args.pool_size,
        "paper_pool_density_tests_per_fault": 4872 / 520,
        "faults_with_trigger_in_pool": len(exposed),
        "headline_whole_corpus": headline,
        "conditional_pool_contains_trigger": conditional,
        "paper_retention_rule_on_pool": paper_regime,
        "faults_under_paper_retention_rule": len(paper_rule),
        "per_fault": per_fault_rates,
        "pool_size_sweep": sweep,
        "fault_corpus": [
            {k: v for k, v in f.items() if k not in ("source", "inputs", "pool_indices")}
            for f in faults
        ],
        "measurement_diagnostics": diagnostics,
        "skipped": skipped,
    }
    if not args.no_save:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"\nSaved: {OUT.relative_to(ROOT)}")

    print(f"\nRuntime: {elapsed:.1f}s")
    print(f"\n{BANNER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
