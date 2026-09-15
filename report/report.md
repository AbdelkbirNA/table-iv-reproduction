# Reproducing Table IV

**Target paper.** Asma Hamidi, Michael Konstantinou, Renzo Degiovanni, Mike Papadakis,
*"How effective are traditional test criteria at detecting bugs in large language models
generated code?"*, [arXiv:2609.09315](https://arxiv.org/abs/2609.09315).

**Scope.** Table IV, HumanEval / GPT-5-mini row: Fault Trigger Rate (FTR) and Fault
Detection Rate (FDR) for mutation score, branch coverage and statement coverage.

**One-line status.** The paper's replication package is not public (evidence in §5), so
this is a **reconstruction, not a faithful reproduction**: the paper's protocol implemented
exactly, run on real GPT-5-mini faults, with substituted artifacts that are named and
carried into every number. FTR is measured; **FDR is not reproduced** and is reported only
as an upper bound (§8).

Every number below came out of an execution recorded under `results/`. Nothing is
estimated or extrapolated. The only numbers not measured here are the paper's own, which
appear in clearly marked *italic* reference columns.

---

## 1. Objective

Benoît's task: take Table IV of arXiv:2609.09315 and try to reproduce it, treating the
exercise as open-ended and permitting simplifying assumptions where they are needed to
reach something meaningful.

The research question Table IV asks is:

> When a test suite is sampled to be *adequate* for a traditional structural criterion
> — statement coverage, branch coverage, or mutation score — how often does that suite
> (a) **trigger** a fault in LLM-generated code, and (b) **detect** it?

The paper's answer is that the criteria trigger a large fraction of faults but detect
almost none, because the oracles are LLM-written. This reproduction asks whether that
result stands up when rebuilt from public artifacts, and reports honestly where it does
not.

## 2. Original Experiment

For each faulty implementation `f` and each adequacy criterion `C`:

1. Start from the pool of generated tests `TS_f`.
2. Consider tests in random order; keep a test only if it increases `C`.
3. Stop when the sampled suite reaches the full pool's value of `C`.
4. Record whether the suite **triggers** the fault (some test's output differs from the
   reference) and whether its **oracle detects** it (some test's own assertion fails).
5. Repeat 100 times; average FTR and FDR over faults and iterations.

Definitions, verbatim from the paper:

```
FTR(T) = |{f ∈ F : triggered(f, T)}| / |F|
FDR(T) = |{f ∈ F : detected(f, T)}| / |F|
```

Retention rule: discard faults with difficulty < 0.75, where difficulty ≥ 0.75 means the
fault is triggered by **at most 25% of the suite**.

Stated generation setup: original and under-specified prompts, 10 generations per prompt
per model, temperature 0.8, 84 difficult HumanEval faults retained for GPT-5-mini, and
**4,872 LLM-Plain tests for HumanEval** across 520 selected faults — a pool density of
**9.37 tests per fault**, which turns out to matter a great deal (§11).

The published HumanEval / GPT-5-mini row, transcribed for comparison only:

| Criterion | FTR | FDR |
|---|---:|---:|
| Mutation | *0.393* | *0.000* |
| Branch | *0.450* | *0.000* |
| Statement | *0.385* | *0.000* |

## 3. Reproduction Environment

| | |
|---|---|
| Python | 3.11.16 (CPython, Clang 21.0.0) |
| Platform | macOS 26.6.2, arm64 (Apple silicon) |
| Coverage tool | `coverage.py` 7.15.4 |
| Mutation tool | `mutmut` 3.7.0 |
| Benchmark | `evalplus` 0.3.1 — HumanEval+ |
| Test runner | `pytest` 9.1.1 |
| Seed | `20260910` (single run-level seed; per-fault seeds derived by SHA-256, `sampling.derive_seed`) |
| Repetitions | 100 per fault and criterion |
| Candidate programs | 319 (164 HumanEval tasks × {original, under-specified}, minus empty generations) |
| Fault corpus | **30 retained**, 289 skipped |
| Pool size `TS_f` | 10 tests per fault (paper density 9.37) |
| Mutants | 624 total, 0–63 per fault |
| Runtime | 427.4 s on the machine above |
| Network | Not required at run time. Benchmark and generation artifacts are fetched once and pinned by checksum (`data/*/manifest.json`). |

No API key is used or needed. The reproduction command is fully offline once artifacts
are fetched.

## 4. Methodology

The implementation separates *behaviour* from *adequacy*, so one execution of a test
feeds all three criteria. Each test is reduced to a `TestObservation`:

1. the set of adequacy items it covers for a criterion,
2. whether it **triggers** the target fault,
3. whether its oracle **detects** the target fault.

The sampler (`src/table_iv_replication/sampling.py`) is then the paper's protocol
verbatim: shuffle, keep a test only if it adds an adequacy item, stop at full-pool
adequacy, repeat 100 times.

**Adequacy items per criterion.**

| Criterion | Items |
|---|---|
| Statement | executed lines of the faulty program, `L12` |
| Branch | executed lines **∪** executed branch arcs, `L12` and `3->4` |
| Mutation | ids of mutants the test kills |

Branch adequacy deliberately **subsumes** statement adequacy. This is the standard
subsumption property (100% branch coverage implies 100% statement coverage) and it is
also how `coverage.py` scores its own branch mode: `Numbers.ratio_covered` is
`(n_executed + n_executed_branches) / (n_statements + n_branches)`. §9 records the bug
that came from getting this wrong, and what fixing it changed.

A branch arc is an adequacy item only when it leaves a **branch point** — a line whose
static exit count exceeds one — and is one of that point's statically possible outcomes.
Purely sequential arcs are excluded, as is an exception escaping a branch point
(`(line, -1)` where no such branch exists); those are retained on `discarded_arcs` rather
than dropped silently. Unreachable and partial branches need no special handling: the
sampling target is the **pool's** adequacy, never a static 100%, so an arc no test in the
pool reaches is never a target.

**Pipeline, end to end** (`scripts/run_table_iv_humaneval_gpt5mini.py`):

1. Load public GPT-5-mini generations for all 164 HumanEval tasks under both prompt
   variants.
2. Split each task's EvalPlus input domain **disjointly and deterministically** into a
   *discovery half* and a *pool half*, so the inputs that establish a fault exists are
   never the inputs its adequate suites are drawn from.
3. Differentially execute candidate against reference across the domain; a candidate is a
   fault iff it mismatches, and iff the discovery half surfaces that mismatch.
4. Build `TS_f` from the pool half by **oracle-blind, coverage-greedy** selection: take
   the input adding the most new statement ∪ branch coverage until nothing adds any, then
   pad to 10. *The selector reads coverage only; it never reads trigger status.*
5. Measure all three criteria on the selected pool; mutation runs only on selected tests.
6. Run the sampling protocol, 100 iterations per fault and criterion.
7. Report the whole corpus, the conditional subset, the paper's retention rule applied
   where the paper defines it, and a pool-size sweep.

**Verification.** `scripts/dry_run_table_iv.py` exercises the whole chain on synthetic
faults with real coverage and mutation measurement, and asserts ten pipeline invariants —
including that detection implies triggering, that FDR never exceeds FTR, that every
sampled suite reaches full-pool adequacy exactly, and that a rerun with the same seed
reproduces every number. All ten pass.

## 5. Available and Missing Artifacts

### 5.1 Available (public, fetched, checksum-pinned)

| Artifact | Source |
|---|---|
| HumanEval+ problems, canonical solutions, adversarial input domain | EvalPlus 0.3.1 |
| Real GPT-5-mini generations, 164 tasks × {original, under-specified} | PromptAnalysis public release, manifest in `data/external/promptanalysis/` |
| The authors' YATE implementation (Java/Kotlin) | `github.com/michaelkonstantinou/yate-java`, pinned at `82b5477…` |
| Published Table IV values | The paper, transcribed into `configs/humaneval_gpt5mini.yaml` |

### 5.2 Reconstructed here

| Artifact | How |
|---|---|
| The LLM-Plain generation protocol (prompts, temperature, repair policy) | Read verbatim out of the authors' pinned Java implementation; written up in `docs/llm_plain_reconstruction.md` |
| `fault_discovery_augmented_tests` | Held-out half of the EvalPlus input domain (assumption A8) |
| `table_iv_llm_plain_tests` (`TS_f`) | Oracle-blind coverage-greedy suite of 10 inputs from the other half (A8) |
| Test oracles | Reference-differential: expected output = reference implementation (A9) |
| Python mutation engine | mutmut 3.7.0 (A2) |

### 5.3 Unavailable — external blockers

| Where I looked | Result |
|---|---|
| arXiv abstract and full HTML | No Data Availability / Artifact section. The text says *"we provide detailed results in our replication package"* but **exposes no URL**. |
| `github.com/michaelkonstantinou/llm-plain` (author's own tool) | **README only, no code.** States: *"Currently an implementation of Java exists, and we are working on Python and Kotlin"* → *"Python implementation: Work in progress"*. |
| `github.com/michaelkonstantinou/yate-java` | Real code, but Java/Kotlin — not the Python pipeline Table IV used. |
| GitHub accounts of all four authors | No replication package for this paper. |

Missing, therefore: the paper's Python LLM-Plain, its test pools, its faulty
implementations, its augmented discovery tests, its mutation tool and operator set, and
its branch-arc convention. **This is an external blocker, not a gap in this repository.**

## 6. Simplifying Assumptions

Every meaningful deviation, with its direction of effect:

| # | Paper | This reproduction | Effect |
|---|---|---|---|
| **A8a** | `fault_discovery_augmented_tests`, LLM-generated | Held-out half of the EvalPlus adversarial input domain | A **stronger** detector than the paper's. Our corpus includes faults the paper's discovery step would have missed, and skews harder. |
| **A8b** | `table_iv_llm_plain_tests`, 4,872 LLM tests / 520 faults | Oracle-blind coverage-greedy suite of 10 real inputs, same density | An **idealisation**: it achieves the coverage objective LLM-Plain is merely *asked* to achieve. Real LLM tests cover less, so our FTR is plausibly optimistic. |
| **A9** | Oracles written by an LLM that saw the faulty program | Reference-differential oracle (correct by construction) | Detection collapses onto triggering. **FDR becomes an upper bound, not a measurement** (§8). |
| **A2** | Python mutation engine, never named in the paper | mutmut 3.7.0 | Operator set drives mutant count → suite size → FTR. Unquantifiable without the paper's tool. |
| **A-gen** | 10 generations per prompt at T=0.8 | 1 public generation per prompt, sampling parameters unrecorded | 30 retained faults against the paper's 84. No highest-difficulty-per-task selection is meaningful with one candidate. |
| **A-diff** | Difficulty measured over a ~9-test suite | Applied on `TS_f`, where the paper defines it — **not** over the fuzzing domain | Measuring it over a 500-input half-domain selects difficulty ≈ 0.999 and measures the denominator rather than the criteria (§11). |

**What was deliberately *not* done.** No parameter was tuned against the published
values, and no published value is readable by any code path. The published figures live
in `configs/humaneval_gpt5mini.yaml`; `tests/test_reference_isolation.py` fails if any
file under `src/`, `scripts/` or `tests/` mentions the config, imports a YAML loader, or
contains any of the fifteen published HumanEval numbers as a literal — and fails
"vacuously safe" too, by asserting the config still holds the values it quarantines.

## 7. Results

Run: `python scripts/run_table_iv_humaneval_gpt5mini.py`, seed 20260910, 100 iterations
per fault and criterion, pool target 10. Full output:
`results/table_iv_humaneval_gpt5mini.json`. Runtime 427.4 s.

**Corpus.** 319 candidate programs; 288 were correct on the observed domain, 1 was faulty
but not surfaced by the held-out discovery half. **30 faults retained.** 624 mutants
across them. Mutation diagnostics clean: **0 baseline mismatches, 0 measurement
failures.**

### 7.1 Headline comparison — whole retained corpus (n = 30)

The differences in this table are computed by `report/make_comparison.py` from the
results file and the quarantined reference values; they are never typed by hand.
`python report/make_comparison.py --check` fails if this block goes stale.

<!-- BEGIN GENERATED: comparison-table (report/make_comparison.py) -->

| Criterion | Reproduced FTR | Paper FTR | Absolute difference |
|---|---:|---:|---:|
| Mutation | **0.3870** | *0.393* | 0.0060 |
| Branch | **0.3493** | *0.450* | 0.1007 |
| Statement | **0.3477** | *0.385* | 0.0373 |

Mean absolute difference **0.0480** over 3 criteria. Measured column: `results/table_iv_humaneval_gpt5mini.json`, seed 20260910, 100 iterations, 30 faults. Paper column transcribed into `configs/humaneval_gpt5mini.yaml`; no pipeline code can read it (`tests/test_reference_isolation.py`).

<!-- END GENERATED: comparison-table -->

Measured suite sizes, and the FDR upper bound, for the same run:

| Criterion | FTR | FDR upper bound | Mean suite size |
|---|---:|---:|---:|
| Mutation | **0.3870** | 0.3870 | 2.53 |
| Branch | **0.3493** | 0.3493 | 2.20 |
| Statement | **0.3477** | 0.3477 | 2.19 |

*FDR here equals FTR by construction (A9) and is **not** comparable to the paper's
0.000. See §8.*

### 7.2 Faults whose pool contains a triggering test (n = 14)

Isolates the question Table IV actually asks about the criteria — *given* that the pool
can expose the fault, does adequate sampling **retain** the exposing test?

| Criterion | FTR | Mean suite size |
|---|---:|---:|
| Mutation | **0.8293** | 2.71 |
| Branch | **0.7486** | 2.36 |
| Statement | **0.7450** | 2.35 |

### 7.3 Paper's retention rule applied where the paper defines it (n = 5)

Difficulty ≥ 0.75 measured over `TS_f` itself, not over a fuzzing domain (§11).

| Criterion | FTR | Mean suite size |
|---|---:|---:|
| Mutation | **0.8280** | 4.06 |
| Branch | **0.8200** | 3.40 |
| Statement | **0.8200** | 3.40 |

### 7.4 Pool-size sensitivity

FTR against pool size, all else fixed (20 sub-pool draws per size below 10, 100
iterations each):

| Pool size | Statement FTR | Branch FTR | Mutation FTR |
|---:|---:|---:|---:|
| 3 | 0.2267 | 0.2642 | 0.2857 |
| 5 | 0.2685 | 0.2828 | 0.3176 |
| 10 | 0.3420 | 0.3410 | 0.3850 |
| 20 | 0.3457 | 0.3390 | 0.3793 |
| 40 | 0.3410 | 0.3443 | 0.3830 |

Sizes above 10 coincide with size 10 because the constructed pool holds 10 tests; the
rows are retained to show the sweep saturating rather than silently clipping.

## 8. FDR Status

**No FDR is reported as a reproduction of the paper's FDR. The measured column labelled
"FDR upper bound" is not comparable to the paper's 0.000 at face value.**

Three quantities must be kept apart:

| Quantity | Value here | What it is |
|---|---|---|
| **Measured FTR** | 0.3870 / 0.3493 / 0.3477 | A real measurement. Comparable to the paper's FTR column. |
| **FDR upper bound** | equal to FTR, by construction | The best any oracle could do on these suites: our oracle is *correct*, so it flags every fault its input triggers. A ceiling, not an estimate. |
| **True FDR under the paper's oracles** | **unavailable** | Requires oracles written by an LLM that has the faulty program in its prompt. |

**Why it could not be reproduced.** FDR is a property of the *oracles*, not of the
sampling. The paper's oracles come from running LLM-Plain, and three things are missing:

1. **The pool artifact.** `table_iv_llm_plain_tests` — the 4,872 HumanEval tests with
   their assertions — is not published (§5.3). It is the single blocking artifact: with
   it, FDR is a direct measurement requiring no model at all.
2. **A working Python LLM-Plain.** The authors' own repository states the Python
   implementation is work in progress; only the Java/Kotlin implementation exists.
3. **Model and credentials.** Regenerating the pool needs an LLM API key, plus the
   paper's exact model snapshot, temperature and repair policy for the *test-generation*
   step. `OPENAI_API_KEY` is not configured in this environment, and the paper does not
   pin the generation configuration for LLM-Plain.

**What was still validated.** The FDR path is not a stub:

- `src/table_iv_replication/generated_test_runner.py` executes real generated test source
  against a faulty program and classifies each test into an explicit `DetectionVerdict`,
  separating "the input triggered the fault" from "the test's own assertion failed".
- The sampling and aggregation path computes FDR for every iteration, and
  `scripts/dry_run_table_iv.py` asserts on real synthetic faults that detection implies
  triggering and that FDR never exceeds FTR — the two invariants FDR must obey.
- `scripts/run_llm_plain_pilot.py` implements the reconstructed LLM-Plain protocol end to
  end. It was executed: it assembled prompts from real faulty sources, hashed them, wrote
  per-candidate manifests, and issued three HTTP requests, which the API rejected (`400`,
  no usable credential). Evidence is committed under `results/llm_plain_pilot/` with
  `response_text: null` — **the pipeline is validated up to the API boundary and no
  further**, and nothing downstream of that boundary has ever produced a number.

**What would complete it.** In order of impact: (1) the paper's `table_iv_llm_plain_tests`
pool, or a working Python LLM-Plain; (2) an LLM API key plus the paper's generation
configuration, which turns the pilot into a measurement; (3) the remaining 9 generations
per prompt at T=0.8, restoring the fault population; (4) the paper's mutation tool and
operator set.

## 9. Comparison with Table IV

**Mutation reproduces closely.** 0.3870 against 0.393 — a difference of 0.0060, well
inside what 30 faults can resolve. Given entirely different faulty programs, a different
test pool and a substituted mutation engine, this level of agreement is the strongest
positive result here.

**Statement is reasonably close.** 0.3477 against 0.385, a difference of 0.0373.
Directionally right, magnitude plausible, but not tight enough to call a reproduction on
its own.

**Branch is the main divergence** — 0.3493 against 0.450, a difference of 0.1007, and the
*ordering* is not reproduced: the paper reports branch as the **best** criterion for
HumanEval / GPT-5-mini, while here branch and statement are statistically
indistinguishable (0.3493 vs 0.3477) and mutation leads.

### 9.1 The branch investigation

The first run of this experiment measured branch FTR **0.2817** — branch was not merely
short of the paper, it was the *worst* of the three criteria, below statement. Branch
coverage subsumes statement coverage, so a branch-adequate suite can never be smaller
than a statement-adequate one; measuring branch *below* statement was a signal that
something was wrong in the implementation, independent of any published value.

What was checked:

| Checked | Finding |
|---|---|
| Branch-arc accounting | Arcs correctly restricted to statically possible outcomes of real branch points; sequential arcs correctly excluded. **No bug.** |
| Exception arcs `(line, -1)` | Correctly excluded from adequacy and retained on `discarded_arcs`. **No bug.** |
| Unreachable / partial branches | The sampling target is the *pool's* adequacy, not a static 100%, so unreached arcs are never targets. **No bug.** |
| Denominator definition | Same greedy-to-full-pool-adequacy rule for all three criteria. **No bug.** |
| `coverage.py` semantics | **Bug found.** See below. |
| Adequacy-item definition vs. the literature | **Bug found.** See below. |
| Corpus-size effects | The gap was stable across all three corpus definitions (n = 30 / 14 / 5) and every pool size — too systematic to be sampling noise. |
| Pool-construction effects | Pool selection is criterion-agnostic (statement ∪ branch) and identical for all three criteria, so it cannot favour one. **No bug.** |

**The bug.** Branch adequacy items were **arcs only**. Arcs alone are not an adequacy
criterion: a *branchless* program has no arcs at all, so its full-pool branch adequacy is
the **empty set**, which the **empty suite** satisfies. The sampler dutifully selected
nothing and the fault scored FTR = 0 by construction, whatever its pool contained.

This hit **8 of the 30 faults**, and in **4** of them the pool did contain a triggering
test that could never be selected:

```
HumanEval/30|under_specified   branch suite size 0.00   pool held 1 triggering test
HumanEval/64|under_specified   branch suite size 0.00   pool held 3 triggering tests
HumanEval/98|under_specified   branch suite size 0.00   pool held 8 triggering tests
HumanEval/108|under_specified  branch suite size 0.00   pool held 8 triggering tests
```

Those four faults have statement FTR 0.10, 0.33, 0.80 and 0.80; forced to 0 for branch,
they account for 0.0677 of deficit, against an observed statement-minus-branch gap of
0.0660. **The entire branch/statement inversion was this one defect.**

**The fix.** Branch adequacy items are now lines **∪** arcs, in
`BranchCoverage.adequacy_items` — the one place all three call sites route through. This
is justified without reference to any target value: it is the textbook subsumption
property, and it is how `coverage.py` computes its own branch-mode percentage
(`(n_executed + n_executed_branches) / (n_statements + n_branches)`). Two regression tests
were added: one asserting statement items are a subset of branch items for the same test,
one asserting a branchless program never admits the empty suite as adequate.

**What the fix changed, and what it did not.** Branch FTR 0.2817 → **0.3493**. Statement
(0.3477) and mutation (0.3870) are **bit-identical** to the pre-fix run, as are their
suite sizes — confirming the change touched only the branch criterion and did not
perturb pool construction. Zero faults now have an empty branch suite, and branch FTR
is ≥ statement FTR for every individual fault, as subsumption requires.

**What remains, honestly.** The fix closes 0.068 of a 0.168 gap. **0.1007 remains
unexplained and is not claimed to be resolved.** The leading hypothesis is that the
divergence is no longer in the branch criterion at all but in the **pool**: our `TS_f` is
a coverage-greedy selection from a real adversarial input domain, which saturates branch
adequacy in ~2.2 tests. Branch coverage can only outrank statement coverage when
satisfying it demands *materially* more tests, and on HumanEval's small functions, with
an idealised pool, it does not — branch buys 0.0016 FTR over statement here. A pool of
real LLM-written tests, which cover less per test and need more of them, is where that
margin would appear. **This is a hypothesis with a mechanism, not a measured cause, and
testing it requires the missing pool artifact.**

**Claim made.** Mutation reproduces. Statement is consistent. **Branch does not
reproduce**, and the reproduction is not described as successful for that criterion.

## 10. Observations

**1. The oracle, not the sampling, is what destroys detection.** With a *correct* oracle,
detection equals triggering: 0.387 for mutation. The paper, sampling the same criteria
over comparable trigger rates, reports FDR **0.000**. The distance between our upper
bound and their measurement is the entire cost of the oracle: **LLM-written oracles lose
essentially 100% of the faults their own inputs trigger.** The coverage criteria are not
what fails in the paper's pipeline — they trigger ~39% of faults. The oracle is. This is
the paper's headline finding, and it survives reconstruction on different artifacts.

**2. Coverage-directed selection does not select for fault exposure.** Only **14 of 30**
oracle-blind, coverage-greedy pools contained *any* triggering test, although every fault
is genuinely faulty and was surfaced by the held-out half. Maximising coverage is close to
orthogonal to exposing these faults — the mechanism behind the paper's finding, observed
here independently.

**3. Mutation costs more and buys little.** Mutation's advantage over statement is +0.039
FTR on the whole corpus and +0.084 conditionally, for suites ~16% larger and the cost of
generating and executing 624 mutants against every test. This reproduces the paper's own
conclusion that *"mutation testing only slightly outperforms coverage criteria despite
higher costs"* — on different artifacts, which is the more informative kind of agreement.

**4. Branch adequacy is nearly free on HumanEval-sized functions.** Post-fix, branch buys
**0.0016 FTR** over statement (0.3493 vs 0.3477) for 0.01 extra tests per suite. On
functions this small, covering every line almost always covers every arc. The paper's
0.450-vs-0.385 branch advantage implies branch adequacy was materially more demanding in
their setup — which points at their *pool*, not at the criterion.

**5. Pool density dominates the absolute numbers.** FTR climbs from 0.23–0.29 at pool
size 3 to 0.34–0.39 at size 10 and then saturates (§7.4). Any Table IV number quoted
without its pool density is not comparable to any other. The paper's 9.37 tests/fault was
matched deliberately for this reason.

**6. A bug that moves a number toward the target is the hardest kind to trust.** The
branch fix improved agreement with the paper. It was adopted because arcs-only adequacy
lets an empty suite be adequate — a defect visible from the internal inconsistency
(branch < statement, which subsumption forbids) with no reference to 0.450. Keeping the
published values unreadable by the pipeline, and enforcing that with a test, is what made
that distinction checkable rather than a matter of self-report.

## 11. Threats to Validity

- **Smaller fault corpus.** 30 retained faults against the paper's 84; the conditional
  and retention-rule subsets rest on 14 and 5. The §7.3 row in particular should be read
  as indicative only. One generation per prompt instead of ten, at unrecorded sampling
  parameters, is the cause.
- **Different fault population.** The paper's faults were *discovered by* LLM-generated
  augmented tests; ours by EvalPlus's adversarial fuzzing domain, a strictly stronger
  detector. Our corpus therefore includes faults the paper's discovery step would have
  missed, and skews harder.
- **Public artifact availability.** None of the paper's own artifacts are public (§5.3).
  Every substitution in §6 is an unquantifiable source of divergence, because there is
  nothing to calibrate against.
- **Model-generation uncertainty.** The faulty programs come from a public GPT-5-mini
  release whose temperature and sampling parameters are unrecorded; the paper used
  T=0.8. Different sampling produces a different fault distribution, and the direction of
  that effect is unknown.
- **The pool is not LLM output.** A coverage-greedy selection from a real input domain is
  an *idealisation* of LLM-Plain. Real LLM-Plain tests would cover less, so our FTR is
  plausibly optimistic — and this is the leading suspect for the residual branch gap
  (§9.1).
- **Branch discrepancy.** 0.1007 after the fix, with the ordering unreproduced. A
  mechanism is proposed in §9.1 but **not measured**; it cannot be tested without the
  missing pool artifact.
- **Difficulty denominator.** The paper measures difficulty over a ~9-test suite.
  Measuring it over a 500-input fuzzing half-domain instead selects a far more extreme
  population (difficulty ≈ 0.999, i.e. 1–2 triggering inputs in 1,000); an earlier run
  with that filter produced pools containing a triggering test in **0 of 7** cases and
  FTR = 0 everywhere, which measures the denominator, not the criteria. §7.3 applies the
  rule where the paper defines it.
- **Mutation engine.** mutmut 3.7.0 stands in for an unnamed tool; the operator set drives
  mutant count, hence suite size, hence FTR. One fault (`HumanEval/161|under_specified`)
  yielded 0 mutants, so its mutation-adequate suite is empty and it contributes 0 to
  mutation FTR — a small deflation of the mutation column.
- **Environment differences.** macOS/arm64, Python 3.11.16. The authors' environment is
  not stated. Coverage and mutation results are platform-insensitive for pure-Python
  targets, but float-tolerant comparisons (`atol`) and timeout behaviour are not
  guaranteed identical.
- **FDR is an upper bound, not a reproduction.** Stated wherever FDR appears (§8).

## 12. Reproducibility Instructions

From a clean checkout:

```bash
git clone https://github.com/AbdelkbirNA/table-iv-reproduction.git
cd table-iv-reproduction

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

python scripts/fetch_related_artifacts.py          # once; 4 files, ~1.7 MB, checksum-pinned
pytest -q                                          # 216 passed

python scripts/run_table_iv_humaneval_gpt5mini.py  # ~7 min, no API key needed
python report/make_comparison.py --check           # report table matches results
```

Optional verification:

```bash
python scripts/dry_run_table_iv.py   # 10 pipeline invariants on synthetic faults
```

| | |
|---|---|
| Writes | `results/table_iv_humaneval_gpt5mini.json` |
| Seed | `20260910`, fixed in the script; override with `--seed` |
| Determinism | Same seed reproduces every number; asserted, not assumed, by `dry_run_table_iv.py` |
| Hidden state | None. The script reads only the pinned EvalPlus benchmark and the pinned generation artifacts, both checksummed against `data/*/manifest.json`. |
| Credentials | None required. `OPENAI_API_KEY` is used only by `scripts/run_llm_plain_pilot.py`, which is not part of this command. |

`--limit N` and `--tasks IDS` run a subset for a fast smoke check.

### 12.1 Clean-state validation performed

These instructions were executed, not assumed. A fresh `git clone` into an empty
directory, with a fresh virtualenv, was taken through every step above:

| Step | Result |
|---|---|
| `pip install -e '.[dev]'` | Clean; resolved to coverage 7.15.4, evalplus 0.3.1, mutmut 3.7.0, pytest 9.1.1 |
| `pytest -q` **before** fetching artifacts | **190 passed, 26 skipped, 0 failed.** The first pass of this validation instead produced 17 hard failures, because `data/external/**` is not committed; tests that need fetched artifacts now skip with the fetch command in the skip reason. |
| `python scripts/fetch_related_artifacts.py` | 4 artifacts fetched and verified against the committed manifest checksums |
| `pytest -q` **after** fetching | **216 passed** |
| `python scripts/run_table_iv_humaneval_gpt5mini.py` | 410.8 s. Every scientific value **bit-identical** to the committed results file — FTR, FDR bound, suite sizes, per-fault rates, corpus, mutant counts, the pool sweep. The only differing fields were per-fault `mutation_seconds` wall-clock timings. |
| `python report/make_comparison.py --check` | Passes |
| `python scripts/dry_run_table_iv.py` | 10/10 invariants pass |

This is the evidence for "no hidden local state": the run was reproduced in a different
directory, from a different virtualenv, against a clone holding only committed files.

**Not validated:** a different operating system, a different CPU architecture, or a
Python other than 3.11.16. `pyproject.toml` declares `>=3.11,<3.14`, but only 3.11.16 on
macOS/arm64 was exercised.

## 13. Conclusion

Table IV's **mutation** FTR reproduces closely (0.3870 vs 0.393) and its **statement** FTR
is consistent (0.3477 vs 0.385), on a fault corpus, test pool and mutation engine that
share none of the paper's artifacts. Its **branch** FTR does not reproduce: 0.3493 vs
0.450, with the paper's branch-best ordering absent. One real implementation defect was
found and fixed during this investigation — arcs-only branch adequacy, which let an empty
suite be branch-adequate for 8 of 30 faults — and it accounted for exactly the portion of
the gap that had pushed branch below statement. The remaining 0.1007 has a proposed
mechanism (an idealised pool on which branch adequacy is nearly free) but **no measured
cause**, and is reported as an open divergence rather than explained away.

**FDR is not reproduced.** It cannot be, without the paper's LLM-Plain test pool or a
working Python implementation of it; both are unavailable and are external blockers. What
the reconstruction contributes instead is a *bound*: with a correct oracle, these same
criteria detect 35–39% of faults, against the paper's 0.000. That gap is the cost of
LLM-written oracles, measured rather than asserted — and it is the paper's central claim,
independently supported.

The honest summary: **a partial reproduction.** Two of three FTR values are consistent
with the paper, one is not, FDR is out of reach, and every substitution that could explain
the difference is named and carried through §6, §9.1 and §11 rather than absorbed into a
success claim.
