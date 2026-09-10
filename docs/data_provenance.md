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
