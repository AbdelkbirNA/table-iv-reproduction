# Data provenance

Two different papers are involved. Keeping them apart is the entire point of
this document.

## A. Target paper -- what we are reproducing

> *How effective are traditional test criteria at detecting bugs in large
> language models generated code?* (Hamidi et al., arXiv:2609.09315)

Table IV of that paper is the reproduction target. Its stated generation setup:

- original prompt **and** under-specified prompt variants
- **10 generations per prompt per model**
- **temperature = 0.8**
- for HumanEval + GPT-5-mini, **84 difficult faults** were retained

**We do not have this paper's replication package.** No URL to it appears in the
paper text available to us. Every artifact below comes from somewhere else.

## B. PromptAnalysis -- a *related* replication package

- Repository: <https://github.com/Amal-AK/PromptAnalysis>
- Pinned commit: `99f5d447aa55167e177069d90f00f81933002e05`
- Fetched by: `scripts/fetch_related_artifacts.py`
- Local copy: `data/external/promptanalysis/` (+ `manifest.json`)

This is the replication package of **related work cited by the target paper**
about defective / under-specified prompts. It is being investigated because it
contains exactly two things the target paper also needs: HumanEval
under-specification mutations, and GPT-5-mini inference results.

> **It is not the Table IV replication package.** Nothing in this repository may
> state or imply that the Table IV authors used these prompts or these
> generations. No evidence of that link has been found. Any experiment built on
> these artifacts is a *reconstruction using related-work artifacts*, and must
> say so wherever its results are reported.

### Fetched artifacts

| File | Bytes | SHA256 |
|---|---:|---|
| `HumanEval_US_mutated.jsonl` | 168 532 | `1a0a995f130b15c347d1c7cbeea5fe4b155252364965586ec789ede04be051c1` |
| `HumanEval.jsonl` | 214 438 | `1d49078ba3e2b196b9344535bef34a43021f038fad9561d6ee7c53450609a6a2` |
| `gpt-5-mini__HumanEval.json` | 624 803 | `42f30fcd5d5309a602737105d4b7b14ffbc802977818f1fbbe10ef6550511ac7` |
| `gpt-5-mini__HumanEval_US_with_tests.json` | 679 867 | `1ff9ab3182e77861f71ca886215e4836e7bdf9e13834f1da2ac68eb7fd625546` |

URLs are pinned to the commit SHA; the fetcher never reads a branch. A local
file whose checksum disagrees with the manifest aborts the run unless `--force`
is passed. Re-verify at any time with:

```bash
python scripts/fetch_related_artifacts.py --verify-only
```

## C. Audit findings

Produced by `scripts/audit_promptanalysis.py` (read-only; no generated code is
executed).

### Prompts

- 164 US records, 164 unique task ids, no duplicates, no malformed records.
  Task ids are **identical** to EvalPlus HumanEval+ (164 tasks, measured).
- `mutation_type` is `US` for all 164. `applicable=True` for 160;
  `HumanEval/23`, `/45`, `/48`, `/53` are `applicable=False` and carry **no**
  `mutated_prompt`.
- `original_prompt` vs the EvalPlus prompt: **163 exact**, 0 formatting-only,
  **1 semantic mismatch** (`HumanEval/115`, where PromptAnalysis moves
  `import math` from module level into the function body).
- The US dataset schema carries **no `entry_point` and no
  `canonical_solution`**; those live in `HumanEval.jsonl` and in the generation
  artifacts.
- All 160 applicable mutated prompts keep the entry point, and the signature
  line is byte-identical to EvalPlus in all 160. The under-specification edits
  the docstring, not the interface.
- 136 of the mutated prompts in the dataset begin with a literal `TASK:` line;
  the generation artifact stores them without it.

### Generations

Both `gpt-5-mini__HumanEval.json` and `gpt-5-mini__HumanEval_US_with_tests.json`
hold **164 records, exactly one generation per task**.

- No `model`, `temperature`, `top_p`, `seed`, `timestamp`, `n`, `sample_index`,
  `finish_reason` or `usage` field exists in either artifact. **Generation
  parameters are not available in these artifacts** and are not inferred
  anywhere in this repository.
- Original prompts: `Eval_Status` OK ×160, `Function not found` ×3, one
  `TypeError`; `Pass@1` true for 158/164 (96.3%); 3 blank `GeneratedCode`.
- US prompts: `Eval_Status` OK ×157, `Function not found` ×6, one
  `AttributeError`; `Pass@1` true for 142/164 (86.6%); 6 blank `GeneratedCode`.
- US correspondence: `PromptUsed` == the record's own `mutated_prompt` for all
  160 applicable tasks (and == `original_prompt` for the 4 non-applicable ones);
  `original_prompt` matches `HumanEval_US_mutated.jsonl` exactly 164/164;
  `mutated_prompt` matches the dataset's, modulo the `TASK:` prefix and leading
  blank lines, for all 160 applicable tasks — **zero semantic divergence**.

### Two different reference implementations

`canonical_solution` in the PromptAnalysis artifacts matches
`HumanEval.jsonl` **164/164** and EvalPlus **0/164** (155 differ in wording, 9
in whitespace only). EvalPlus rewrites HumanEval's canonical solutions, so
"the reference implementation" is ambiguous until we pick one. This repository's
instrumentation currently uses the **EvalPlus** canonical solution. Whichever is
chosen must be stated wherever fault triggering is reported, because it defines
expected behaviour.

### The embedded `TestCases` are benchmark tests

`TestCases` is byte-identical to the record's own `test` field, to
`HumanEval.jsonl`'s `test`, and to the EvalPlus `test` field — **164/164 in both
generation artifacts**. It carries the stock HumanEval `check()` function,
`METADATA = {'author': 'jt', ...}` header included.

> These are the **HumanEval benchmark tests**. They are **not** an
> LLM-generated test pool and must never be treated as LLM-Plain output.

## E. Reference-oracle equivalence audit

Fault classification compares a generated program's behaviour against *a*
reference implementation, and HumanEval has two in circulation. This audit asks
whether the choice can change a trigger label.

**Result: it can. The two references are not interchangeable.**

### Method

`scripts/audit_reference_equivalence.py` (module:
`src/table_iv_replication/reference_oracle.py`).

- **Reference A**, the candidate: `prompt + canonical_solution` from
  PromptAnalysis `HumanEval.jsonl`, i.e. the original HumanEval canonical
  solution (matches PromptAnalysis's generation artifacts 164/164).
- **Reference B**, the expectation: `prompt + canonical_solution` from EvalPlus
  HumanEval+.
- **Test domain**: `base_input + plus_input` from EvalPlus, all 164 tasks,
  **124 253 inputs**; both directions are run (the oracle is asymmetric), for
  **248 138 executions**.
- **Isolation**: both references run in a child process with a 1 s per-input
  limit, reusing the streamed-worker orchestration built for mutation testing
  (`target.run_streamed_worker`). A task is abandoned after 5 timed-out inputs
  so a pathological reference cannot stall the audit.

### Comparator

`table_iv_replication._output_oracle.compare_outputs` mirrors the per-input
oracle inside `evalplus.eval.unsafe_execute` (evalplus 0.3.1), and **reuses**
EvalPlus's own `is_floats` and `_special_oracle._poly` rather than
reimplementing them:

1. `out == exp` -> accept.
2. HumanEval/32 (`find_zero`): accept iff `abs(_poly(*inp, out)) <= atol` --
   any root counts, so the two references may legitimately return different
   values.
3. Otherwise, if `atol == 0 and is_floats(exp)`, raise `atol` to `1e-6`.
4. With a non-zero tolerance: require `type(out) is type(exp)`, equal length for
   sequences, then `np.allclose(out, exp, rtol=1e-07, atol=atol)`.
5. An exception is an outcome: the same exception *type* on both sides is
   agreement, a different type is a disagreement.
6. A **timeout is not an observation**. It is recorded as unobserved, never as a
   difference.

Two documented deviations from upstream, both deliberate:

- EvalPlus reassigns `atol` inside its input loop, so one float expectation
  raises the tolerance for every later input of the same task. Here the 1e-6
  floor is applied per comparison. The two agree except where a non-float pair
  is unequal yet numerically close, which cannot arise for HumanEval's return
  types.
- EvalPlus treats a timeout as a test failure; this audit treats it as an
  unobserved input, because the two references failing to be compared is not
  evidence that they differ.

### Findings (164 tasks, 137.7 s)

| Classification | Tasks |
|---|---:|
| `SOURCE_IDENTICAL` | 0 |
| `SOURCE_DIFFERENT_BEHAVIOR_EQUIVALENT_ON_TEST_DOMAIN` | **142** |
| `BEHAVIOR_MISMATCH` | **17** |
| `EXECUTION_INCONCLUSIVE` | **5** |

Disagreeing inputs: 1 245 by value, 8 by exception type. Forward and reverse
directions classified every task identically, so no result depends on which
reference is passed as the oracle's `exp`.

**17 tasks where the two references compute different answers.** Full detail in
`results/reference_equivalence.json`.

| Task | Entry point | Disagree / compared | Example |
|---|---|---:|---|
| `HumanEval/22` | `filter_integers` | 166/956 | `[True, False, None, 0, -10, ...]` -> original keeps the booleans, EvalPlus drops them |
| `HumanEval/32` | `find_zero` | 123/886 | original's root fails the polynomial oracle at `atol=1e-4` on large coefficients |
| `HumanEval/44` | `change_base` | 8/478 | `(0, 3)` -> original `''`, EvalPlus `'0'` |
| `HumanEval/49` | `modp` | 1/990 | |
| `HumanEval/64` | `vowels_count` | 1/999 | original raises `IndexError` where EvalPlus returns |
| `HumanEval/76` | `is_simple_power` | 3/907 | |
| `HumanEval/91` | `is_bored` | 1/1006 | sentence splitting differs |
| `HumanEval/95` | `check_dict_case` | 46/524 | |
| `HumanEval/97` | `multiply` | 441/985 | |
| `HumanEval/111` | `histogram` | 42/663 | |
| `HumanEval/122` | `add_elements` | 151/1005 | |
| `HumanEval/123` | `get_odd_collatz` | 7/143 | |
| `HumanEval/124` | `valid_date` | 16/1001 | |
| `HumanEval/125` | `split_words` | 2/812 | |
| `HumanEval/132` | `is_nested` | 30/1014 | `'[][][]'` -> original `True`, EvalPlus `False` |
| `HumanEval/140` | `fix_spaces` | 31/1005 | |
| `HumanEval/150` | `x_or_y` | 184/1008 | |

**5 inconclusive tasks, all for the same reason:** the original HumanEval
solution is too slow for EvalPlus's extended inputs. In every case the timeout
is on the original side (`left=timeout right=ok`).

| Task | Compared | Unobserved | Cause |
|---|---:|---|---|
| `HumanEval/31` | 166 | 1 timeout | `is_prime` trial division on `123456791` |
| `HumanEval/39` | 11 | 1 timeout | `prime_fib` |
| `HumanEval/55` | 16 | 5 timeouts + 29 skipped | naive recursive `fib` |
| `HumanEval/63` | 27 | 5 timeouts + 38 skipped | `fibfib` exponential recursion, `n` up to 102 |
| `HumanEval/127` | 485 | 5 timeouts + 98 skipped | `intersection` over `[0, 100000007]` |

They agreed on every input that *was* observed; inconclusive means unobserved,
not different.

### What this means, and what is not being decided here

Per the research policy, **no reference is being selected**. The audit's job was
to find out whether the choice matters, and it does:

- For **142 of 164 tasks** the two references are observationally equivalent on
  this domain, so either produces the same trigger labels there.
- For **17 tasks** they compute different answers, on up to 44% of the input
  domain (`HumanEval/97`). A generated program matching one reference will be
  labelled faulty against the other. These 17 tasks would contaminate FTR
  directly.
- For **5 tasks** the original reference cannot be executed over the full
  domain, so it cannot serve as an oracle there at all.

The divergences are edge cases the original HumanEval tests never reached --
booleans counting as integers, `change_base(0, 3)`, `'[][][]'` — which is
precisely what EvalPlus's extended inputs were built to expose. That is evidence
about *which* reference is more careful, not proof of which one the Table IV
authors used. **The decision is yours.**

### Limitation

Agreement on this domain is **observational equivalence over the available
inputs, not mathematical program equivalence**. 142 tasks agreeing on 124 253
inputs means no *available* input distinguishes them; an input outside the
domain still could. Nothing here licenses the phrase "the two references are
equivalent" without the qualifier.

## D. Still missing for a faithful HumanEval / GPT-5-mini Table IV run

1. **10 generations per prompt per task.** We have 1. Nine tenths of the fault
   population the paper draws from does not exist in these artifacts.
2. **Sampling parameters.** Temperature 0.8 is the paper's; these artifacts
   record no temperature at all, so we cannot confirm how they were produced.
3. **The LLM-generated test pool (`TS_f`).** The single largest gap: Table IV
   samples suites *from generated tests*, and no such pool exists here.
4. **The 84 retained difficult faults**, and the criterion used to retain them.
5. **The mutation engine and operator set** (open as assumption A2).
6. **Any evidence linking these generations to the Table IV experiment.**
   Until such evidence exists, results built on them are a reconstruction,
   not a reproduction.
