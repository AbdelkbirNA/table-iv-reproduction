# Table IV Reproduction

Reproduction study for Table IV of **"How effective are traditional test criteria at detecting bugs in large language models generated code?"** (Hamidi et al., arXiv:2609.09315).

## Goal

Reproduce, or meaningfully approximate when the original artifacts are unavailable, the Table IV comparison of:

- mutation score
- branch coverage
- statement coverage

using:

- Fault Trigger Rate (FTR)
- Fault Detection Rate (FDR)

The paper samples a coverage-maximizing test suite randomly, repeats the process 100 times per fault and criterion, and reports mean FTR/FDR by benchmark and fault-generating model.

## Current status

The paper's replication package is not public (checked: arXiv text, the authors'
`llm-plain` repo, all four authors' GitHub accounts -- `report/report.md` §2). The
protocol is therefore implemented exactly and run on real GPT-5-mini faults with two
documented substitutions: the sampling pool (A8) and the oracle (A9).

**Measured**, 30 real faults, 100 iterations, seed 20260910
(`results/table_iv_humaneval_gpt5mini.json`):

| Criterion | FTR measured | *Paper FTR* | FDR upper bound | *Paper FDR* |
|---|---:|---:|---:|---:|
| Mutation | **0.3870** | *0.393* | 0.3870 | *0.000* |
| Branch | **0.2817** | *0.450* | 0.2817 | *0.000* |
| Statement | **0.3477** | *0.385* | 0.3477 | *0.000* |

Mutation lands within 0.006 of the paper and statement within 0.037; branch diverges by
0.168 and inverts the ordering, discussed in the report. **FDR here is an upper bound,
not a reproduction** -- our oracle is correct, so it detects everything it triggers. The
paper's oracles were LLM-written with the faulty program in the prompt. The gap between
0.387 and 0.000 is the cost of the oracle, and it is the main finding.

Full write-up: **`report/report.md`**.

## Phase 1 scope

Start with **HumanEval / GPT-5-mini** only. Target values reported in Table IV:

| Criterion | FTR | FDR |
|---|---:|---:|
| Mutation | 0.393 | 0.000 |
| Branch | 0.450 | 0.000 |
| Statement | 0.385 | 0.000 |

These values are references for comparison, not values hard-coded into the experiment.

## Design

The core sampler is intentionally independent of the raw benchmark infrastructure. Each test case is represented by:

1. the set of adequacy items it covers for a chosen criterion (statements, branches, or killed mutants),
2. whether it triggers the target fault,
3. whether its oracle detects the target fault.

This allows the Table IV sampling protocol to be tested before all original paper artifacts are available.

## Important methodological assumptions

The arXiv paper does not identify the exact Python mutation engine/operators used, and it does not expose a replication-package URL in the paper text. The official LLM-Plain repository currently says the implementation available through YATE is Java-oriented and its Python implementation is work in progress, so we do not possess the `table_iv_llm_plain_tests` pool (the paper reports 4,872 LLM-Plain tests for HumanEval). Therefore, any replacement mutation engine or regenerated test pool must be documented as a simplifying assumption.

The paper generates tests with an LLM at two distinct points, and this repository keeps them apart: `fault_discovery_augmented_tests` (differential tests that define the fault corpus and the paper's difficulty) and `table_iv_llm_plain_tests` (the pool Table IV samples adequate suites from). See `docs/experiment_plan.md`.

## Local setup

Recommended: Python 3.11 in an isolated environment.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q                                              # 182 passed

python scripts/run_table_iv_humaneval_gpt5mini.py      # the reproduction, ~9 min
python scripts/dry_run_table_iv.py                     # pipeline invariants, synthetic
```

Generated or untrusted code should be executed only inside a sandbox/container.

## Repository layout

```text
src/table_iv_replication/   Core sampling and metrics
scripts/                   Experiment entry points
configs/                   Reproduction configurations
data/                      External/raw artifact notes (not committed when large)
results/                   Raw and processed outputs
tests/                     Unit tests for the reproduction logic
docs/                      Protocol and assumption log
report/                    The written report
```

## Reproducibility policy

Every experiment should record:

- dataset version/revision
- model identifier
- random seed
- number of iterations
- test-pool source
- mutation tool and version
- coverage tool and version
- all deviations from the original paper
