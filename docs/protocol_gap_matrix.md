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
| **`fault_discovery_augmented_tests`** | LLM-generated differential tests per implementation; forms the augmented suite that defines the fault corpus | **Nothing** | EvalPlus input domain used as the denominator instead | **Low** — different denominator, therefore a different difficulty | ❌ **Blocker** |
| **Difficulty >= 0.75 retention** | Discard faults with difficulty < 0.75; retain difficulty >= 0.75 (triggered by at most 25% of the suite) | Rule implemented with the correct direction and inclusive bound; pinned by test | `evalplus_domain_difficulty >= 0.75` → `provisional_difficult_candidate` | **Rule exact, denominator wrong** | ⚠️ Correct rule, wrong input |
| **Highest-difficulty fault per task** | Where a task has several retained faults, keep the one with the highest difficulty | Not performed | none | n/a — meaningless with 1 candidate per variant | ❌ Blocked by generations |
| **LLM-Plain Python test generation** | LLM-Plain generates the sampling pool | The **public Java/Kotlin** YATE Plain workflow, read from a pinned commit: prompts, temperature 0.1, 5 repair iterations, 1 generation request, no coverage phase (`docs/llm_plain_reconstruction.md`) | Prompt builders + protocol model in `llm_plain_protocol.py`, tagged `INFERRED_ADAPTATION`; no API client | **Medium for the shape, none for the parameters** — every Table IV value is UNKNOWN | ❌ **Blocker** (needs a model + the paper's parameters) |
| **`table_iv_llm_plain_tests` (4,872 HumanEval tests)** | 4,872 LLM-Plain tests for HumanEval; `TS_f` is the pool suites are sampled from | **Nothing.** 4872/520 ≈ 9.37 tests per selected fault is an aggregate ratio only, not a per-fault count | none | **None** | ❌ **Blocker** |
| **Statement coverage** | Per-test statement adequacy on the program under test | Implemented, isolated per test, arbitrary source, validated | coverage.py 7.15.4 | **Exact** | ✅ Ready |
| **Branch coverage** | Per-test branch adequacy on the program under test | Implemented; branch points from static analysis, sequential and exception arcs excluded | coverage.py 7.15.4 | **Exact** | ✅ Ready |
| **Mutation engine** | Mutation adequacy; tool and operator set **not stated in the paper** | Implemented with per-test kill sets, structured outcomes, subprocess isolation | mutmut 3.7.0 (assumption A2) | **Unknown** — operator set drives mutant count, which drives suite size and FTR | ⚠️ Provisional |
| **FTR** | Fraction of faults whose sampled suite contains a triggering test | Trigger semantics implemented and validated; sampler implemented | none | **Machinery ready, inputs missing** | ❌ Blocked by pool + generations |
| **FDR** | Fraction of faults whose sampled suite's **oracle** detects the fault | **Implemented and validated** on hand-written suites: `generated_test_runner.py` separates triggering from detection, records entry-point calls by runtime proxy, and classifies faulty-biased / invalid / inconclusive oracles explicitly | none needed for the mechanism | **Exact mechanism; conservative detection policy documented (A7)** | ⚠️ Ready, awaiting generated tests |
| **100 randomized repetitions** | Randomized coverage-increasing sampling, 100 repetitions per fault and criterion | Implemented (`sampling.run_repetitions`), deterministic seeds, tested, and exercised end-to-end by `scripts/dry_run_table_iv.py` across all three criteria | none needed | **Exact** | ✅ Ready |

## Reading the matrix

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
