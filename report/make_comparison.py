"""Generate the report's Table IV comparison block from measured results.

Every derived number in `report/report.md` -- the absolute differences against
the published figures -- is computed here, never typed by hand. The block lives
between HTML comment markers and is rewritten in place.

    python report/make_comparison.py            # print the block
    python report/make_comparison.py --write    # rewrite it into report.md
    python report/make_comparison.py --check    # fail if report.md is stale

**Why this lives in `report/`, not `scripts/`.** The published Table IV values
are quarantined in `configs/`, and `tests/test_reference_isolation.py` fails if
anything under `src/`, `scripts/` or `tests/` can read them -- a pipeline that
can see its target can be tuned to it. This tool must read them, so it sits
outside the scanned tree and runs strictly *after* measurement, on a results
file it cannot influence. It also parses the config by regex rather than
importing a YAML loader, so the "no YAML dependency" half of that guard holds.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "table_iv_humaneval_gpt5mini.json"
CONFIG = ROOT / "configs" / "humaneval_gpt5mini.yaml"
REPORT = ROOT / "report" / "report.md"

BEGIN = "<!-- BEGIN GENERATED: comparison-table (report/make_comparison.py) -->"
END = "<!-- END GENERATED: comparison-table -->"
CRITERIA = ("mutation", "branch", "statement")


def paper_reference(model: str = "gpt-5-mini") -> dict[str, dict[str, float]]:
    """Read one model's published FTR/FDR row out of the config, by regex."""
    line = next(
        (
            raw
            for raw in CONFIG.read_text(encoding="utf-8").splitlines()
            if raw.strip().startswith(f"{model}:")
        ),
        None,
    )
    if line is None:
        raise SystemExit(f"{model} not found in {CONFIG}")
    found = {
        criterion: {"ftr": float(ftr), "fdr": float(fdr)}
        for criterion, ftr, fdr in re.findall(
            r"(\w+): \{ftr: ([0-9.]+), fdr: ([0-9.]+)\}", line
        )
    }
    missing = set(CRITERIA) - set(found)
    if missing:
        raise SystemExit(f"config row for {model} is missing {sorted(missing)}")
    return found


def render() -> str:
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    measured = results["headline_whole_corpus"]
    paper = paper_reference()

    rows = [
        "| Criterion | Reproduced FTR | Paper FTR | Absolute difference |",
        "|---|---:|---:|---:|",
    ]
    for criterion in CRITERIA:
        ours = measured[criterion]["ftr"]
        theirs = paper[criterion]["ftr"]
        rows.append(
            f"| {criterion.capitalize()} | **{ours:.4f}** | *{theirs:.3f}* "
            f"| {abs(ours - theirs):.4f} |"
        )

    mean = sum(
        abs(measured[c]["ftr"] - paper[c]["ftr"]) for c in CRITERIA
    ) / len(CRITERIA)
    rows += [
        "",
        f"Mean absolute difference **{mean:.4f}** over {len(CRITERIA)} criteria. "
        f"Measured column: `results/table_iv_humaneval_gpt5mini.json`, seed "
        f"{results['seed']}, {results['repetitions']} iterations, "
        f"{results['faults_retained']} faults. Paper column transcribed into "
        f"`configs/humaneval_gpt5mini.yaml`; no pipeline code can read it "
        f"(`tests/test_reference_isolation.py`).",
    ]
    return "\n".join(rows)


def splice(text: str, block: str) -> str:
    start, end = text.index(BEGIN), text.index(END)
    return text[:start] + f"{BEGIN}\n\n{block}\n\n" + text[end:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite report.md")
    parser.add_argument("--check", action="store_true", help="fail if stale")
    args = parser.parse_args()

    block = render()
    if not (args.write or args.check):
        print(block)
        return 0

    text = REPORT.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"generated-block markers missing from {REPORT}")
    updated = splice(text, block)

    if args.check:
        if updated != text:
            print("report.md comparison table is stale; run --write", file=sys.stderr)
            return 1
        print("report.md comparison table matches the measured results")
        return 0

    REPORT.write_text(updated, encoding="utf-8")
    print(f"wrote comparison table into {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
