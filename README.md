# Reproducing Table IV of arXiv:2609.09315

Reproduction study of **Table IV** of Hamidi, Konstantinou, Degiovanni & Papadakis,
*"How effective are traditional test criteria at detecting bugs in large language models
generated code?"* — [arXiv:2609.09315](https://arxiv.org/abs/2609.09315).

## Objective

Table IV asks: when a test suite is sampled to be **adequate** for a traditional
structural criterion — statement coverage, branch coverage, or mutation score — how often
does it **trigger** a fault in LLM-generated code (FTR), and how often does it **detect**
one (FDR)? This repository re-implements that protocol and runs it on real GPT-5-mini
faults.

## What was reproduced

The paper's replication package is not public — no artifact URL in the paper, the authors'
own `llm-plain` repository is README-only with *"Python implementation: Work in
progress"*. So this is a **reconstruction, not a faithful reproduction**: the protocol
implemented exactly, real GPT-5-mini faults, substituted test pool and oracle, every
substitution named in the report.

- **FTR is measured.** Real numbers, 30 real faults, 100 iterations, fixed seed.
- **FDR is *not* reproduced.** It depends on the paper's LLM-written oracles, which are
  unavailable. Only an upper bound is reported, and it is never compared to the paper's
  FDR at face value. See report §8.

## Headline results

30 faults, 100 iterations, seed 20260910 (`results/table_iv_humaneval_gpt5mini.json`):

| Criterion | Reproduced FTR | *Paper FTR* | Absolute difference |
|---|---:|---:|---:|
| Mutation | **0.3870** | *0.393* | 0.0060 |
| Branch | **0.3493** | *0.450* | 0.1007 |
| Statement | **0.3477** | *0.385* | 0.0373 |

Mutation reproduces closely and statement is consistent. **Branch does not reproduce**:
the paper reports branch as the best criterion, here it is indistinguishable from
statement. A real implementation bug was found and fixed during that investigation
(arcs-only branch adequacy let an *empty* suite count as branch-adequate for 8 of 30
faults); it closed 0.068 of the gap and 0.101 remains open and unexplained — report §9.1.

*Italic values are transcribed from the paper for comparison only. No code path can read
them; `tests/test_reference_isolation.py` fails if that ever changes. The differences
above are computed by `report/make_comparison.py`, not typed by hand.*

## Reproduce it

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

pytest -q                                          # 216 passed
python scripts/run_table_iv_humaneval_gpt5mini.py  # ~7 min, no API key needed
```

Writes `results/table_iv_humaneval_gpt5mini.json`. Deterministic: the same seed reproduces
every number, and `python scripts/dry_run_table_iv.py` asserts that plus nine other
pipeline invariants on synthetic faults.

## Limitations

- **FDR is unavailable** — needs the paper's `table_iv_llm_plain_tests` pool or a working
  Python LLM-Plain. Neither exists publicly.
- **Branch diverges by 0.1007** and the paper's criterion ordering is not reproduced.
- **30 faults, not 84** — public generations give 1 per prompt, the paper used 10 at T=0.8.
- **Substituted pool and oracle** (A8, A9) and **substituted mutation engine** (A2, mutmut
  3.7.0 — the paper never names its tool).

All of these, with their direction of effect, are in report §6 and §11.

## Repository layout

```text
src/table_iv_replication/   Sampling protocol, coverage/mutation measurement, oracles
scripts/                    Experiment entry points (no published value is readable here)
configs/                    Run configuration + quarantined Table IV reference values
data/                       Pinned external artifacts (checksummed manifests)
results/                    Measured outputs, committed
tests/                      216 unit tests, including the reference-isolation guard
docs/                       Protocol reconstruction, gap matrix, assumption log
report/                     The report, and the tool that generates its comparison table
```

## Report

**[`report/report.md`](report/report.md)** — objective, methodology, artifact
availability, assumptions, results, FDR status, the branch investigation, observations,
threats to validity, and reproduction instructions.
