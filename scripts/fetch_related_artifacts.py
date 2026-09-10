"""Fetch the PromptAnalysis artifacts we audit, pinned to one commit.

PromptAnalysis is the replication package of *related work* cited by the Table
IV paper -- not the Table IV replication package. See docs/data_provenance.md.

    python scripts/fetch_related_artifacts.py [--force] [--verify-only]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from table_iv_replication.artifact_fetch import Source, main  # noqa: E402

PROMPTANALYSIS = Source(
    name="promptanalysis",
    repo="Amal-AK/PromptAnalysis",
    commit="99f5d447aa55167e177069d90f00f81933002e05",
    paths=(
        "datasets/humanEval/HumanEval_US_mutated.jsonl",
        "datasets/humanEval/HumanEval.jsonl",
        "inference_results/openai/gpt-5-mini__HumanEval.json",
        "inference_results/openai/gpt-5-mini__HumanEval_US_with_tests.json",
    ),
    note=(
        "Replication package of related work cited by the Table IV paper, NOT the "
        "Table IV replication package. See docs/data_provenance.md."
    ),
)


if __name__ == "__main__":
    raise SystemExit(main(PROMPTANALYSIS, description=__doc__))
