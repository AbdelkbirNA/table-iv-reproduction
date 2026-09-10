# Experiment plan

## Paper protocol to preserve

For each faulty implementation and each adequacy criterion:

1. Start with the full generated test pool for that fault (`TS_f`, the
   **`table_iv_llm_plain_tests`** pool -- not the fault-discovery augmented
   tests; see below).
2. Randomly consider tests.
3. Keep a test only when it increases the target adequacy criterion.
4. Stop when the sampled suite reaches the same target-criterion value as the full pool.
5. Evaluate whether the sampled suite triggers the real LLM-generated fault (FTR contribution).
6. Evaluate whether the test oracle detects the real fault (FDR contribution).
7. Repeat 100 times.
8. Aggregate mean FTR/FDR by benchmark and LLM fault model.

## Adequacy target -- experimental assumption A1

**Decision: the adequacy criterion is measured against each faulty
implementation `f`, not against the canonical reference.**

Rationale. The paper's protocol opens with *"Given a faulty implementation f and
the pool of generated tests TS_f..."* and then samples tests according to
whether they increase criterion C. The program whose statements, branches and
mutants are being covered is therefore the program under test, i.e. `f`.

Consequences for the pipeline:

- Statement and branch adequacy items are line numbers / branch arcs **of the
  faulty source file**. Two faults for the same task generally have different
  line numbering, so adequacy items are only comparable within one fault.
- The canonical implementation is used **only for behavioural comparison**:
  running it on an input yields the expected output, which decides whether a
  test triggers the fault (`TestObservation.triggers_fault`). It never
  contributes adequacy items.
- The coverage layer (`src/table_iv_replication/target.py`,
  `statement_coverage.py`, `branch_coverage.py`) therefore accepts arbitrary
  target source, an entry point name and test inputs. No HumanEval code is
  hard-coded into it.
- `scripts/smoke_statement_coverage.py` and `scripts/smoke_branch_coverage.py`
  run against the HumanEval/0 canonical solution as **infrastructure validation
  only**; the canonical source merely plays the role of a program under test in
  those smoke checks.

**This is our interpretation, not a documented statement from the paper.** If a
replication package later reveals a different convention (for example adequacy
computed once against the reference solution, or against a shared instrumented
harness), only the call sites change: the sampler in `sampling.py` consumes
opaque `adequacy_items` and is unaffected. Re-run the pipeline with the other
convention and report both if the difference is material.

## Coverage instrumentation decisions

1. **Per-test isolation.** Each test input runs in its own `coverage.Coverage`
   session scoped with `include=[<target file>]`. Arguments are deep-copied
   before the session starts so `copy` and EvalPlus internals never enter the
   data.
2. **Statement items** are the executed line numbers of the target file.
   Module-level lines (imports, `def`) execute at import time, before any
   session, so no individual test can claim them. The full-pool target is the
   union over tests, so this is self-consistent, but the denominator is smaller
   than "all executable statements" -- state it whenever a raw coverage
   percentage is reported.
3. **Branch items** are executed arcs `(origin, destination)` where `origin` is
   a branch point (`PythonParser.exit_counts()[origin] > 1`) *and* the arc is
   one of that point's statically possible outcomes. Sequential arcs and the
   synthetic entry/exit arcs (`-1`) are not adequacy items.
4. **Exception exits are excluded.** An exception escaping a branch point is
   recorded by coverage.py as `(origin, -1)` even when the source has no such
   branch. Counting it would inflate the full-pool target with an outcome no
   correct execution can reach. Such arcs are retained on
   `BranchCoverage.discarded_arcs` for inspection, never as adequacy.
5. **Runtime errors are recorded, not swallowed.** A crashing test keeps the
   coverage it accumulated up to the raise point and carries the exception on
   `.error`. This matters: LLM-generated faults frequently crash rather than
   return a wrong value.

## Reference oracle -- decision A4 (resolved)

Fault triggering is decided by comparing a generated program against a reference
implementation. HumanEval has two: the original dataset's canonical solution
(shipped verbatim by PromptAnalysis) and EvalPlus's rewritten one.

**Primary oracle: the EvalPlus 0.3.1 HumanEval+ canonical implementation.**

Rationale:

- The target paper explicitly studies HumanEval+.
- HumanEval+ is cited via EvalPlus together with the original HumanEval.
- The paper states that the reference solution provided by the dataset serves
  as ground truth.
- Our audit found the original HumanEval reference disagrees with the EvalPlus
  HumanEval+ reference on **17 tasks** and is inconclusive on another **5**
  because the original solution is too slow for the extended input domain
  (`docs/data_provenance.md` section E; `results/reference_equivalence.json`).

**This is not a proof that EvalPlus's implementation is the one the authors
used.** No artifact we hold states which reference they ran. A4 records a
reasoned choice, not a verified fact.

**Secondary sensitivity oracle: the original HumanEval canonical solution.**
Every fault label is computed against both references and the disagreements are
reported, so the 17 divergent tasks can never silently drive a result. See
`scripts/classify_public_gpt5mini_faults.py` and
`results/public_gpt5mini_fault_summary.json`.

## Fault difficulty -- assumption A5

The target paper defines fault difficulty as the complement of the proportion of
tests that trigger the fault:

```
difficulty = 1 - (tests that trigger the fault / tests in the suite)
```

It then **discards faults whose difficulty is lower than 0.75**, i.e. **retains
faults with difficulty >= 0.75** -- faults triggered by at most 25% of the
tests, the hard-to-catch ones. Where a task has several retained faults, it
keeps the one with the **highest** difficulty. For HumanEval + GPT-5-mini it
reports 84 retained faults.

> Correction, 2026-09-10: an earlier draft of this section said the paper keeps
> difficulty *below* 0.75. That was wrong, and it was wrong only in prose --
> `fault_classifier.CandidateResult.provisional_difficult_candidate` has always
> tested `difficulty >= DIFFICULTY_THRESHOLD` (0.75), so no computed result was
> affected. `tests/test_fault_classifier.py` now pins the boundary explicitly.

We compute:

```
trigger_ratio            = triggering inputs / observed inputs
evalplus_domain_difficulty = 1 - trigger_ratio
```

**over the EvalPlus HumanEval+ input domain only**, and we call it
`evalplus_domain_difficulty` everywhere -- never "fault difficulty".

The two are not the same quantity. The paper measures difficulty over the
**fault-discovery augmented suite** (see the next section); we measure it over
the EvalPlus input domain. A different denominator gives a different difficulty,
so a candidate at 0.80 here could sit either side of the paper's threshold
there.

Candidates at `evalplus_domain_difficulty >= 0.75` are reported as
**`provisional_difficult_candidate`** -- the same direction as the paper's
retention rule, on a different denominator. It is a diagnostic count only. These
are *not* "selected Table IV faults", and no per-task selection (keep the
highest-difficulty fault) is performed: with one generation per prompt variant
instead of ten, there is no population to select the most difficult member from.

## Two distinct LLM-generated test processes -- do not conflate

The paper generates tests with an LLM at two different points, for two different
purposes. Confusing them corrupts both the fault corpus and the sampling pool,
so each has its own name in this repository.

### A. `fault_discovery_augmented_tests`

Per generated implementation, **additional differential tests** are generated to
expose behavioural differences against the reference implementation. They form
the *augmented suite* used to:

- identify which generated implementations are faulty,
- compute the paper's **fault difficulty**,
- apply the **difficulty >= 0.75** retention filter,
- select the **highest-difficulty fault per task**.

This suite is upstream of Table IV. It decides *which faults exist*. It is
**not** the pool Table IV samples from, and it must never be called the
LLM-Plain pool.

**What we have:** nothing. Our fault labelling uses the EvalPlus HumanEval+
input domain as a stand-in denominator, which is why our metric is named
`evalplus_domain_difficulty` and never "fault difficulty".

### B. `table_iv_llm_plain_tests`

Separately, the paper uses **LLM-Plain** to generate the **test pool** from which
statement-, branch- and mutation-adequate suites are randomly sampled. The paper
reports **4,872 LLM-Plain tests for HumanEval**. This pool is `TS_f` in the
protocol at the top of this document, and it is the only pool Table IV's
randomized sampling draws from.

**What we have:** nothing. See the LLM-Plain availability note in
`docs/data_provenance.md`.

The distinction matters concretely: the fault corpus depends on A, the FTR/FDR
numbers depend on B, and a suite sampled from A would answer a different
question than Table IV asks.

## Generated-test evaluation -- decisions A6 and A7

### A6: repair policy follows the public implementation

The Plain workflow repairs whatever the test runner surfaces -- compile errors
and failing assertions alike -- up to five times
(`CONFIRMED_PUBLIC_IMPLEMENTATION`; see `docs/llm_plain_reconstruction.md`
section J). Our `PUBLIC_YATE_FAITHFUL` config does the same. Whether the
unpublished Python Table IV implementation did is
`UNKNOWN_TARGET_PAPER_IMPLEMENTATION`, and `ALTERNATIVE_SENSITIVITY_CONFIG`
(assertion repair off) exists to measure how much that choice moves FDR.

### A7: triggering and detection are computed separately, detection conservatively

A test **triggers** a fault when at least one recorded invocation of the entry
point yields a different observable outcome on the faulty program than on the
reference, under the same comparator used for every other trigger decision in
this project. Inputs are captured by proxying the entry point at runtime, not by
parsing values out of the test source, so a test making several calls with
arbitrary expressions is handled like a literal one.

A test **detects** a fault only when it passes on the reference *and* its own
assertion fails on the faulty program *and* an input genuinely distinguishes the
two. Nothing else counts: an oracle that agrees with the faulty output
(`FAULTY_BIASED_ORACLE`), an oracle wrong on both programs (`INVALID_ORACLE`), a
broken test, a timeout, and a fault-induced crash
(`DETECTION_INCONCLUSIVE`) all yield `detects_fault = False`. FDR is
under-reported rather than inflated where causality is unclear.

### Table IV uses base suites, never RQ3's regenerated oracles

Table IV / RQ1 evaluates the **original** LLM-generated oracles. RQ3 separately
regenerates oracles for triggering tests from the input plus the natural-language
specification (not the faulty code), repairing runtime errors up to five times.
Those are different artifacts answering different questions; RQ3 output must
never feed a Table IV number.

## Mutation adequacy -- experimental assumptions A2 and A3

### A2: mutmut 3.7.0 is a provisional engine, not the authors' tool

The paper defines mutation testing conceptually but **does not identify the
Python mutation engine or the operator set** it used. We do not currently have
that information. `mutmut==3.7.0` is pinned in this project and used as the
mutation engine **as a provisional reproduction choice only**. Nothing in this
repository claims mutmut reproduces the authors' operators.

Consequences and mitigations:

- Mutant counts, and therefore mutation-adequate suite sizes, depend on the
  operator set. A different engine (mutpy, cosmic-ray, a hand-rolled operator
  set) would produce a different number of mutants and plausibly different FTR.
  Any Table IV number produced with this adapter must be reported as
  "mutation adequacy under mutmut 3.7.0", not as the paper's mutation criterion.
- The engine is isolated behind `src/table_iv_replication/mutation_coverage.py`.
  Replacing it means implementing `generate_mutants(source, entry_point) ->
  (list[Mutant], module_source)`; the kill loop, the sampler and the metrics are
  engine-agnostic.
- If the replication package later names another tool or operator set, swap the
  adapter and re-run. Record both results if they differ materially.

### A3: full-pool mutation adequacy, and what a survivor is

The equivalent-mutant problem is undecidable and we do not attempt to solve it.
Following the paper's generic stopping condition -- a sampled suite is adequate
when it reaches the same criterion value as the full pool -- we define:

> **full-pool mutation adequacy** = the set of mutants killed by at least one
> test in the full pool.

Sampling then retains a test when it kills a mutant not already killed, and
stops when the selected tests kill exactly that set. Mutants no test in the pool
kills are excluded from the target, so sampling always terminates.

Such mutants are recorded as **observational survivors**
(`MutationRun.survivors`). An observational survivor is *not* a proven
equivalent mutant: it may simply need an input the pool does not contain. The
test suite demonstrates the difference -- a mutant that survives a three-input
pool is killed once the missing input is added. Never report survivors as
equivalent mutants, and never report `killed / total_mutants` as a mutation
score without stating which pool produced it.

### Adequacy is measured against the program under test

Consistent with assumption A1: mutants are generated from the **program under
test**. In the smoke tests that is HumanEval/0's canonical solution; in the real
fault experiments it will be **the selected faulty implementation**, and mutant
ids are only comparable within one fault. The canonical implementation is used
solely for behavioural comparison.

### Mutation execution model

1. **One mutated module, not one file per mutant.**
   `mutmut.mutation.file_mutation.mutate_file_contents` emits a single module
   holding the original function, every mutant as a mangled function, and a
   dispatcher. One import serves all mutants; the active one is selected per
   call. HumanEval/0 costs 13 078 (mutant, input) executions in ~0.3 s.
2. **mutmut's runtime is not imported.** mutmut's own trampoline imports
   `mutmut.__main__`, which resolves a process-global, CWD-dependent config at
   import time. We replace that single import line with an equivalent inlined
   dispatcher. The mutant *bodies* -- the scientifically relevant part -- are
   mutmut's output verbatim. A guard raises if mutmut's emitted code stops
   matching that shape, so a mutmut upgrade fails loudly instead of silently.
3. **Fidelity check.** Every input is also run through the mutated module with
   no mutant active. Any disagreement with the clean original module is reported
   as `baseline_mismatches` -- i.e. mutmut's rewrite changed behaviour on its
   own. It must be empty for a run to be trusted.
4. **Kill rule on structured outcomes.** `Outcome(kind, value)` where kind is
   `return` / `exception` / `timeout` / `crash`. A mutant is killed when its
   outcome differs from the original's. An exception is an outcome, not an
   error: original `ValueError` vs mutant `ValueError` is **not** a kill. Return
   values are compared by `repr`, which is exact for the int/float/str/list/bool
   returns HumanEval uses but would need revisiting for objects with
   address-bearing or unstable reprs.
5. **Isolation and timeouts.** All target execution happens in a child process
   (`_mutation_worker.py`), never in the research process -- required once the
   targets are LLM-generated. Each call is bounded by `SIGALRM`
   (`timeout`, default 1 s); a timeout is a recorded outcome, hence a kill. The
   worker streams one flushed result line per item, and the parent kills it if
   output stalls, records the in-flight item as a failure, and restarts on the
   remainder. A pathological mutant therefore costs one item, not the run.
   Known limit: `SIGALRM` cannot interrupt a hang inside a C call; the parent's
   stall detector is the backstop. POSIX only.

## Phase 1

HumanEval + GPT-5-mini.

**The HumanEval+ inputs currently exercised by `scripts/smoke_*.py` are
smoke-test data only.** They validate the instrumentation; they are not the
paper's test pool. Table IV samples from the `table_iv_llm_plain_tests` pool for
each fault, which we have not yet obtained or reconstructed. No number produced from the
HumanEval+ input pool may be presented as a reproduction of Table IV.

The paper reports 84 selected non-trivial HumanEval faults for GPT-5-mini.

## Artifacts we still need

- selected faulty GPT-5-mini HumanEval implementations
- `table_iv_llm_plain_tests`: the LLM-Plain test pool associated with each selected fault
- `fault_discovery_augmented_tests`: the differential tests used to identify faults and compute the paper's difficulty
- original test assertions/oracles
- per-test statement/branch coverage data (implemented: computed directly from each target source)
- mutation operator/tool details, or a justified substitute

## Public building blocks already identified

- EvalPlus / HumanEval+ for benchmark ground truth and rigorous tests
- SERVAl's LLMEval-Dataset for HumanEval/MBPP prompt variants (candidate source for defective prompts; not yet confirmed as the exact artifact used here)
- coverage.py for statement and branch instrumentation
- mutmut 3.7.0 as the provisional Python mutation engine (assumption A2); used as a mutant *generator*, not as a test runner

## Known ambiguity log

1. The paper does not identify the exact Python mutation tool/operator set -- see **assumption A2** above. mutmut 3.7.0 is a provisional stand-in behind a replaceable adapter.
2. The paper mentions a replication package but does not expose a URL in the PDF/HTML text currently available.
3. LLM-Plain's public repository says Python support is work in progress, so reproducing the exact `table_iv_llm_plain_tests` pool from that repository is not currently possible. See the LLM-Plain availability note in `docs/data_provenance.md`.
4. The paper does not expose the prompt or configuration used for the `fault_discovery_augmented_tests`, so the denominator of its fault difficulty cannot be reconstructed.
4. ~~We must confirm whether adequacy is computed against each faulty implementation or another program variant~~ -- resolved as **assumption A1** above: adequacy is measured against the faulty implementation. Still an assumption, not a confirmed convention; instrumentation stays outside the sampling core so it can be switched without rewriting the protocol.
