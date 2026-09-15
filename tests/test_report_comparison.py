"""The report's comparison table must match the measured results.

`report/make_comparison.py` computes every derived number in §7.1 of the report
from `results/` and the quarantined reference values. This test runs its
`--check` mode, so a rerun that changes a measured value cannot leave a stale
table sitting in the report.

The generator lives outside `src/`, `scripts/` and `tests/` on purpose: it is
the only tool allowed to read the published figures, and
`test_reference_isolation.py` keeps that separation enforced.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "report" / "make_comparison.py"


def test_generator_exists_outside_the_pipeline():
    assert GENERATOR.is_file()
    assert GENERATOR.parent.name == "report"


def test_report_comparison_table_is_not_stale():
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
