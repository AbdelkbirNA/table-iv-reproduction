"""Per-test statement coverage against an arbitrary target program.

Each test input is executed under its own coverage session so the executed
source lines can be attributed to that individual test. Those line sets are the
``adequacy_items`` consumed by the sampling protocol.

The target is any Python source string -- in the reproduction it is the faulty
implementation under test, not the canonical reference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import coverage

from .target import (
    ExecutionRecord,
    load_entry_point,
    run_under_coverage,
    target_program,
    write_target,
)

__all__ = [
    "TestCoverage",
    "executable_lines",
    "load_entry_point",
    "measure_all",
    "measure_one",
    "measure_source",
    "union_lines",
    "write_target",
]


@dataclass(frozen=True)
class TestCoverage:
    """Statement coverage observed for one test input."""

    __test__ = False

    index: int
    lines: frozenset[int]
    result: Any = None
    error: str | None = None
    measured_files: frozenset[str] = frozenset()

    @property
    def adequacy_items(self) -> frozenset[str]:
        """Criterion items in the form consumed by TestObservation."""
        return frozenset(f"L{line}" for line in self.lines)

    @classmethod
    def from_record(cls, record: ExecutionRecord) -> "TestCoverage":
        return cls(
            index=record.index,
            lines=record.lines,
            result=record.result,
            error=record.error,
            measured_files=record.measured_files,
        )


def executable_lines(path: Path) -> frozenset[int]:
    """Executable statements of `path` according to coverage.py's parser."""
    cov = coverage.Coverage(data_file=None, config_file=False)
    _, statements, _, _, _ = cov.analysis2(str(path))
    return frozenset(statements)


def measure_one(
    fn: Callable[..., Any],
    args: Sequence[Any],
    path: Path,
    index: int = 0,
    *,
    restrict_to_target: bool = True,
) -> TestCoverage:
    record = run_under_coverage(
        fn, args, path, index, branch=False, restrict_to_target=restrict_to_target
    )
    return TestCoverage.from_record(record)


def measure_all(
    fn: Callable[..., Any],
    inputs: Sequence[Sequence[Any]],
    path: Path,
) -> list[TestCoverage]:
    return [measure_one(fn, args, path, index) for index, args in enumerate(inputs)]


def measure_source(
    source: str,
    entry_point: str,
    inputs: Sequence[Sequence[Any]],
) -> tuple[list[TestCoverage], frozenset[int]]:
    """Measure per-test statement coverage of arbitrary source.

    Returns the per-test observations and the file's executable statements.
    """
    with target_program(source, entry_point) as (path, fn):
        return measure_all(fn, inputs, path), executable_lines(path)


def union_lines(observations: Sequence[TestCoverage]) -> frozenset[int]:
    covered: set[int] = set()
    for observation in observations:
        covered.update(observation.lines)
    return frozenset(covered)
