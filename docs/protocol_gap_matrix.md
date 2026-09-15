# Protocol gap matrix

What the target paper's Table IV protocol requires, against what this
repository actually possesses, as of 2026-09-10.

*Fidelity* is how close the substitute is to the paper's component.
*Status* is whether that component currently blocks a faithful reproduction.

| Component | Paper protocol | What we possess | Current substitute | Fidelity | Status |
|---|---|---|---|---|---|
| **HumanEval+ benchmark** | HumanEval+ (164 tasks) as the benchmark | EvalPlus 0.3.1, 164 tasks, 124 253 inputs, loaded and validated | none needed | **Exact** | ✅ Ready |
| **Reference implementation** | "the reference solution provided by the dataset" serves as ground truth | Both circulating references, behaviourally audited against each other | EvalPlus HumanEval+ canonical solution (decision A4); original HumanEval kept as sensitivity oracle | **High, unverified** — the paper never says which reference it ran; the two disagree on 17 tasks | ⚠️ Decided, not proven |
| **Defective / under-specified prompts** | Original prompt + under-specified prompt variants | PromptAnalysis `HumanEval_US_mutated.jsonl`: 164 records, 160 applicable, entry point and signature preserved | PromptAnalysis US prompts | **Unknown** — structurally compatible, but from related work; no evidence the Table IV authors used these | ⚠️ Plausible substitute |
| **GPT-5-mini generations** | 10 generations per prompt per task, temperature 0.8 | **1** generation per prompt per task (328 total), no model/temperature/seed recorded | PromptAnalysis public inference results | **Low** — one tenth of the population, unverifiable sampling parameters | ❌ **Blocker** |
| **`fault_discovery_augmented_tests`** | LLM-generated differential tests per implementation; forms the augmented suite that defines the fault corpus | **Nothing** | **A8**: held-out half of the EvalPlus input domain, disjoint from the sampling pool | **Low** — a fuzzing domain is a strictly stronger fault detector than an LLM suite, so the corpus skews harder | ⚠️ Substituted, documented |
| **Difficulty >= 0.75 retention** | Discard faults with difficulty < 0.75; retain difficulty >= 0.75 (triggered by at most 25% of the suite) | Rule implemented with the correct direction and inclusive bound; pinned by test | Now measured **over `TS_f` itself**, where the paper defines it. Measuring it over a 500-input fuzzing half-domain instead selects difficulty ≈ 0.999 and drove FTR to 0 everywhere (0 of 7 pools exposed a fault); corrected. | **Rule exact, denominator now comparable** | ✅ Corrected |
| **Highest-difficulty fault per task** | Where a task has several retained faults, keep the one with the highest difficulty | Not performed | none | n/a — meaningless with 1 candidate per variant | ❌ Blocked by generations |
| **LLM-Plain Python test generation** | LLM-Plain generates the sampling pool | The **public Java/Kotlin** YATE Plain workflow, read from a pinned commit: prompts, temperature 0.1, 5 repair iterations, 1 generation request, no coverage phase (`docs/llm_plain_reconstruction.md`) | Prompt builders + protocol model in `llm_plain_protocol.py`, tagged `INFERRED_ADAPTATION`; no API client | **Medium for the shape, none for the parameters** — every Table IV value is UNKNOWN | ❌ **Blocker** (needs a model + the paper's parameters) |
| **`table_iv_llm_plain_tests` (4,872 HumanEval tests)** | 4,872 LLM-Plain tests for HumanEval; `TS_f` is the pool suites are sampled from | **Nothing.** 4872/520 ≈ 9.37 tests per selected fault is an aggregate ratio only, not a per-fault count | **A8**: oracle-blind coverage-greedy suite of 10 real inputs per fault, matching that density. Mirrors LLM-Plain's stated objective ("100% code coverage", program in the prompt) without ever reading trigger status | **Medium** — an *idealisation* of LLM-Plain: it attains the coverage objective LLM-Plain is only asked to attain, so FTR is plausibly optimistic | ⚠️ Substituted, documented |
| **Statement coverage** | Per-test statement adequacy on the program under test | Implemented, isolated per test, arbitrary source, validated | coverage.py 7.15.4 | **Exact** | ✅ Ready |
| **Branch coverage** | Per-test branch adequacy on the program under test | Implemented; branch points from static analysis, sequential and exception arcs excluded | coverage.py 7.15.4 | **Exact** | ✅ Ready |
| **Mutation engine** | Mutation adequacy; tool and operator set **not stated in the paper** | Implemented with per-test kill sets, structured outcomes, subprocess isolation | mutmut 3.7.0 (assumption A2) | **Unknown** — operator set drives mutant count, which drives suite size and FTR | ⚠️ Provisional |
| **FTR** | Fraction of faults whose sampled suite contains a triggering test | Trigger semantics implemented and validated; sampler implemented | **Measured** on 30 real GPT-5-mini faults: mutation 0.387, statement 0.348, branch 0.282 (`results/table_iv_humaneval_gpt5mini.json`) | **Mutation within 0.006 of the paper, statement within 0.037, branch off by 0.168** | ✅ Measured under A8 |
| **FDR** | Fraction of faults whose sampled suite's **oracle** detects the fault | `generated_test_runner.py` separates triggering from detection and classifies faulty-biased / invalid oracles explicitly | **A9**: reference-differential oracle. It is correct, so detection equals triggering and FDR = FTR by construction | **Upper bound only** — the paper's oracles were LLM-written with the faulty program in the prompt; ours cannot be compared to theirs at face value | ⚠️ Bound measured; the paper's FDR needs an LLM |
| **100 randomized repetitions** | Randomized coverage-increasing sampling, 100 repetitions per fault and criterion | Implemented (`sampling.run_repetitions`), deterministic seeds, tested, and exercised end-to-end by `scripts/dry_run_table_iv.py` across all three criteria | none needed | **Exact** | ✅ Ready |

## Reading the matrix

**Updated after the first real run (`results/table_iv_humaneval_gpt5mini.json`).**

The measurement machinery was never the blocker: three adequacy criteria, isolated
execution, the sampler, the metrics. What blocked Table IV was artifacts, and the
paper's artifacts remain unavailable — its replication package exposes no URL, and
the authors' own `llm-plain` repository still says the Python implementation is work
in progress.

Two substitutions (A8 pool, A9 oracle) now let the protocol run end to end on real
GPT-5-mini faults and produce real FTR numbers. What each substitution costs is
stated in its row and in `report/report.md` §6.

One row cannot be closed without an LLM: **FDR**. Measuring it as the paper does
requires test *assertions* written by a model that has seen the faulty program. Every
pool we can build without an LLM supplies inputs whose oracle is correct, and a
correct oracle detects everything it triggers. That makes our FDR an upper bound and
the paper's FDR unreachable from here — which is itself the finding: the distance
between our 0.387 and the paper's 0.000 is the entire cost of the oracle.

The measurement machinery is done: three adequacy criteria, isolated execution,
the sampler, the metrics. Nine of fourteen rows are either exact or a documented
provisional choice.

What blocks Table IV is **artifacts, not code**. Five rows are hard blockers,
and two of them are the same missing thing seen twice: the LLM-Plain pool that
FTR and FDR are computed over. Without it there is no suite to sample, so no
FTR/FDR number can be produced at any fidelity. The generation shortfall (1 of
10) and the absent augmentation tests together mean the fault corpus itself is
not the paper's corpus.

FDR is the one row where nothing exists yet in either artifacts or code, because
it needs test *assertions* — an oracle — and every pool we could build so far
supplies only inputs.
