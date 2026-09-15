# Reproducing Table IV of arXiv:2609.09315

**Target paper.** Asma Hamidi, Michael Konstantinou, Renzo Degiovanni, Mike Papadakis,
*"How effective are traditional test criteria at detecting bugs in large language models
generated code?"*, arXiv:2609.09315.

**Target.** Table IV, HumanEval / GPT-5-mini row: Fault Trigger Rate (FTR) and Fault
Detection Rate (FDR) for mutation score, branch coverage and statement coverage.

**Status.** The paper's replication package is not public (evidence in §2). A faithful
reproduction is therefore impossible. What follows is a **reconstruction**: the paper's
protocol implemented exactly, run on real GPT-5-mini faults, with two substituted
artifacts that are named, justified and carried into every number reported.

Every number in this report was produced by an execution recorded in
`results/`. Nothing is estimated, extrapolated or copied from the paper except
the clearly-marked reference column.

---

## 1. What the paper does

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
**9.37 tests per fault**, which turns out to matter a great deal (§6).

## 2. Artifact availability — what was actually checked

| Where I looked | Result |
|---|---|
| arXiv abstract and full HTML | No Data Availability / Artifact section. The text says *"we provide detailed results in our replication package"* but **exposes no URL**. |
| `github.com/michaelkonstantinou/llm-plain` (author's own tool) | **README only, no code.** States: *"Currently an implementation of Java exists, and we are working on Python and Kotlin"* → *"Python implementation: Work in progress"*. |
| `github.com/michaelkonstantinou/yate-java` | Real code, but Java/Kotlin. Pinned at `82b5477…`; its Plain-LLM workflow is reconstructed in `docs/llm_plain_reconstruction.md`. |
| GitHub repos of all four authors | No replication package for this paper. |

The paper's Python LLM-Plain, its test pools, its faulty implementations and its
augmented discovery tests are **all unavailable**. This is an external blocker, not a
gap in this repository.

## 3. What was substituted, and why

| Paper component | Substitute | Assumption |
|---|---|---|
| Faulty GPT-5-mini implementations, 10 gens/prompt @ T=0.8 | PromptAnalysis public GPT-5-mini generations, **1 generation per prompt** (164 tasks × {original, under-specified}) | Real LLM output, one tenth of the population, sampling parameters unrecorded |
| `fault_discovery_augmented_tests` | Held-out half of the EvalPlus HumanEval+ input domain | A8 |
| `table_iv_llm_plain_tests` (`TS_f`) | **Oracle-blind, coverage-greedy suite** of 10 inputs drawn from the other half | A8 |
| Generated test oracles | Reference-differential oracle (expected output = reference implementation) | A9 |
| Python mutation engine (unnamed in the paper) | mutmut 3.7.0 | A2 |

### A8 — the pool

LLM-Plain's generation prompt, read verbatim from the authors' own public implementation,
is *"generate all tests needed to achieve 100% code coverage"* with the program under test
pasted into the prompt. Its output is therefore a **small, coverage-oriented suite written
without sight of the reference solution**.

The substitute mirrors that objective on real inputs: greedily take the input adding the
most new statement∪branch coverage of the faulty program until nothing adds any, then pad
to 10 tests (LLM-Plain emits redundant tests too). **The selector reads coverage only — it
never reads whether an input triggers the fault.** A triggering input enters the pool only
incidentally, exactly as with a generator that cannot see the reference.

The domain is split disjointly and deterministically so the suite that establishes a fault
exists is not the suite its adequate subsets are drawn from — the separation the paper
relies on and that `docs/experiment_plan.md` has insisted on from the start.

### A9 — the oracle, and why our FDR is not the paper's

Our tests carry the reference implementation's output as their expectation. **Such an
oracle is correct, so it flags every fault its input triggers: FDR = FTR by construction.**

This is not a reproduction of the paper's FDR. It is an **upper bound** on it. The paper's
oracles were written by an LLM that had the faulty program in its prompt, so they encode
the buggy behaviour and detect nothing. The two FDR columns must never be compared at face
value. What the comparison *does* buy is a quantified bound — see §5.

## 4. Results

Run: `scripts/run_table_iv_humaneval_gpt5mini.py`, seed 20260910, 100 iterations per fault
and criterion, pool target 10 tests. Full output: `results/table_iv_humaneval_gpt5mini.json`.
Runtime 551 s.

**Corpus.** 319 candidate programs; 288 were correct on the observed domain, 1 was faulty
but not surfaced by the held-out discovery half. **30 faults retained.** 624 mutants
generated across them (0–63 per fault). Mutation diagnostics were clean: **0 baseline
mismatches, 0 measurement failures.**

### 4.1 Whole retained corpus (n = 30)

| Criterion | FTR (measured) | FDR upper bound (measured) | Mean suite size | *Paper FTR* | *Paper FDR* |
|---|---:|---:|---:|---:|---:|
| Mutation | **0.3870** | 0.3870 | 2.53 | *0.393* | *0.000* |
| Branch | **0.2817** | 0.2817 | 1.93 | *0.450* | *0.000* |
| Statement | **0.3477** | 0.3477 | 2.19 | *0.385* | *0.000* |

*Italic columns are transcribed from the paper for comparison only. They are not read by
any code path — `grep -rn "paper_target\|humaneval_gpt5mini\|yaml" src/ scripts/ tests/`
returns nothing.*

### 4.2 Faults whose pool contains a triggering test (n = 14)

Isolates the question Table IV actually asks about the criteria — *given* that the pool can
expose the fault, does adequate sampling **retain** the exposing test?

| Criterion | FTR | Mean suite size |
|---|---:|---:|
| Mutation | **0.8293** | 2.71 |
| Statement | **0.7450** | 2.35 |
| Branch | **0.6036** | 2.08 |

### 4.3 Paper's retention rule applied where the paper defines it (n = 5)

Difficulty ≥ 0.75 measured over `TS_f` itself, not over a fuzzing domain (see §6).

| Criterion | FTR | Mean suite size |
|---|---:|---:|
| Mutation | **0.8280** | 4.06 |
| Statement | **0.8200** | 3.40 |
| Branch | **0.8000** | 3.20 |

### 4.4 Pool-size sensitivity

FTR against pool size, all else fixed (20 sub-pool draws per size, 100 iterations each):

| Pool size | Statement FTR | Branch FTR | Mutation FTR |
|---:|---:|---:|---:|
| 3 | 0.2267 | 0.1968 | 0.2857 |
| 5 | 0.2685 | 0.2184 | 0.3176 |
| 10 | 0.3420 | 0.2743 | 0.3850 |
| 20 | 0.3457 | 0.2780 | 0.3793 |
| 40 | 0.3410 | 0.2760 | 0.3830 |

Sizes above 10 coincide with size 10 because the constructed pool holds 10 tests; the row
is retained to show the sweep saturating rather than silently clipping.

## 5. Observations

**1. Mutation reproduces closely; branch does not.** Mutation FTR **0.387 vs 0.393** is
within 0.006 of the paper. Statement **0.348 vs 0.385** is within 0.037. Branch **0.282 vs
0.450** is off by 0.168 — the one large divergence, and it inverts the ordering: the paper
reports branch as the *best* criterion for HumanEval/GPT-5-mini, while here it is
consistently the *worst*, across all three corpus definitions (§4.1–4.3) and every pool
size (§4.4). The agreement of two criteria and the stability of the branch gap suggest a
genuine methodological difference rather than noise. The most likely cause is branch-arc
accounting: this repository excludes sequential arcs and exception arcs `(origin, -1)` from
adequacy (`docs/experiment_plan.md`), which makes branch adequacy cheaper to satisfy —
smaller suites (1.93, the smallest of the three) and so fewer retained tests. The paper does
not state its arc convention. **This is a hypothesis, not a measured cause; it is untested.**

**2. Mutation costs more and buys little.** Mutation's advantage over statement is
+0.039 FTR on the whole corpus and +0.084 conditionally, for suites ~16% larger and the
cost of generating and executing 624 mutants against every test. This reproduces the
paper's own conclusion that *"mutation testing only slightly outperforms coverage criteria
despite higher costs"* — on different artifacts, which is the more informative kind of
agreement.

**3. The oracle, not the sampling, is what destroys detection.** This is the substitution
turned into a measurement. With a *correct* oracle, detection equals triggering: FDR 0.387
for mutation. The paper, sampling the same criteria over comparable rates, reports FDR
**0.000**. The gap between our upper bound and their measurement is the entire cost of the
oracle: **LLM-written oracles lose ~100% of the faults their own inputs trigger.** Coverage
criteria are not what fails in the paper's pipeline — they trigger ~39% of faults. The
oracle is.

**4. Coverage-directed selection does not select for fault exposure.** Only **14 of 30**
oracle-blind, coverage-greedy pools contained any triggering test, although every fault is
genuinely faulty and was surfaced by the held-out half. Maximising coverage is close to
orthogonal to exposing these faults — the mechanism behind the paper's headline finding,
observed here independently.

## 6. Threats to validity

- **Different fault population.** The paper's faults were *discovered by* LLM-generated
  augmented tests; ours were discovered by EvalPlus's adversarial fuzzing domain, a
  strictly stronger detector. Our corpus therefore includes faults the paper's discovery
  step would have missed, and skews harder.
- **Difficulty denominator.** The paper measures difficulty over a ~9-test suite. Measuring
  it over a 500-input fuzzing half-domain instead selects a far more extreme population
  (difficulty ≈ 0.999, i.e. 1–2 triggering inputs in 1,000); an earlier run with that
  filter produced pools containing a triggering test in **0 of 7** cases and FTR = 0
  everywhere, which measures the denominator, not the criteria. §4.3 applies the rule where
  the paper defines it. This is the `protocol_gap_matrix.md` row "rule exact, denominator
  wrong", now corrected.
- **One generation per prompt, not ten.** 30 retained faults against the paper's 84. No
  highest-difficulty-per-task selection is meaningful with one candidate per variant.
- **Pool is not LLM output.** A coverage-greedy selection from a real input domain is an
  *idealisation* of LLM-Plain — it achieves the coverage objective LLM-Plain is merely asked
  to achieve. Real LLM-Plain tests would cover less, so our FTR is plausibly optimistic.
- **Mutation engine.** mutmut 3.7.0 stands in for an unnamed tool; the operator set drives
  mutant count, hence suite size, hence FTR. One fault (`HumanEval/161|under_specified`)
  yielded 0 mutants, so its mutation-adequate suite is empty and it contributes 0 to
  mutation FTR — a small deflation of the mutation column in §4.1.
- **FDR is an upper bound, not a reproduction.** Stated wherever FDR appears.
- **Small n.** 30 / 14 / 5 faults. The §4.3 row in particular rests on 5 faults and should
  be read as indicative only.

## 7. What would close the gap

In order of impact:

1. **The paper's `table_iv_llm_plain_tests` pool**, or a working Python LLM-Plain. This is
   the single blocking artifact: it is the only way to measure FDR as the paper measures it.
2. **An LLM API key.** With one, `scripts/run_llm_plain_pilot.py` can generate a real
   test pool with real LLM-written oracles using the protocol reconstructed in
   `docs/llm_plain_reconstruction.md`, turning the FDR upper bound into a measurement.
   Currently `OPENAI_API_KEY` is unset in the environment, in every shell profile, in a
   login shell and in any project `.env`; the pilot stops at the key gate.
3. **The remaining 9 generations per prompt** at temperature 0.8, restoring the fault
   population and the highest-difficulty-per-task rule.
4. **The paper's mutation tool and operator set**, replacing A2.

## 8. Reproducing this report

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q                                              # 182 passed
python scripts/run_table_iv_humaneval_gpt5mini.py      # ~9 min, writes results/
```

Deterministic: rerunning with the same `--seed` reproduces every number above. Determinism
is verified rather than asserted by `scripts/dry_run_table_iv.py`, which exercises the whole
chain on synthetic artifacts with real coverage and mutation measurement and checks ten
pipeline invariants, including that a rerun reproduces every number.
