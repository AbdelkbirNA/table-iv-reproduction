"""Per-test branch coverage against an arbitrary target program.

coverage.py records every executed *arc* ``(from_line, to_line)``, including
purely sequential control flow and the synthetic function entry/exit arcs
(``-1``). Those are not branch outcomes and must not become adequacy items.

A line is a **branch point** when coverage.py's static parser gives it more than
one possible exit (``PythonParser.exit_counts()[line] > 1``) -- this is the same
definition coverage.py uses for its own branch report. A **branch adequacy item**
is an executed arc that leaves a branch point *and* is one of that point's
statically possible outcomes, kept as the stable pair
``(source_line, destination_line)``. Everything else is discarded.

The second condition matters: an exception propagating out of a branch point is
recorded by coverage.py as ``(line, -1)`` even when no such branch exists in the
source. Those arcs are not adequacy items -- they would otherwise inflate the
full-pool target with an outcome no correct execution can reach. They are kept
on ``BranchCoverage.discarded_arcs`` rather than dropped silently.

The target is any Python source string -- in the reproduction it is the faulty
implementation under test, not the canonical reference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .target import ExecutionRecord, parse_target, run_under_coverage, target_program

__all__ = [
    "BranchCoverage",
    "branch_points",
    "measure_all",
    "measure_one",
    "measure_source",
    "possible_branch_arcs",
    "union_arcs",
]

Arc = tuple[int, int]


@dataclass(frozen=True)
class BranchCoverage:
    """Branch coverage observed for one test input."""

    __test__ = False

    index: int
    arcs: frozenset[Arc]
    lines: frozenset[int] = frozenset()
    result: Any = None
    error: str | None = None
    measured_files: frozenset[str] = frozenset()
    discarded_arcs: frozenset[Arc] = frozenset()

    @property
    def adequacy_items(self) -> frozenset[str]:
        """Criterion items in the form consumed by TestObservation."""
        return frozenset(f"{origin}->{destination}" for origin, destination in self.arcs)

    @classmethod
    def from_record(cls, record: ExecutionRecord, possible: frozenset[Arc]) -> "BranchCoverage":
        points = {origin for origin, _ in possible}
        from_branch_point = frozenset(arc for arc in record.arcs if arc[0] in points)
        return cls(
            index=record.index,
            arcs=from_branch_point & possible,
            lines=record.lines,
            result=record.result,
            error=record.error,
            measured_files=record.measured_files,
            discarded_arcs=from_branch_point - possible,
        )


def branch_points(path: Path) -> frozenset[int]:
    """Lines that have more than one possible successor (real decision points)."""
    exit_counts = parse_target(path).exit_counts()
    return frozenset(line for line, exits in exit_counts.items() if exits > 1)


def possible_branch_arcs(path: Path) -> frozenset[Arc]:
    """Every branch outcome the target *could* take, per static analysis."""
    parser = parse_target(path)
    points = {line for line, exits in parser.exit_counts().items() if exits > 1}
    return frozenset(arc for arc in parser.arcs() if arc[0] in points)


def measure_one(
    fn: Callable[..., Any],
    args: Sequence[Any],
    path: Path,
    index: int = 0,
    *,
    possible: frozenset[Arc] | None = None,
    restrict_to_target: bool = True,
) -> BranchCoverage:
    if possible is None:
        possible = possible_branch_arcs(path)
    record = run_under_coverage(
        fn, args, path, index, branch=True, restrict_to_target=restrict_to_target
    )
    return BranchCoverage.from_record(record, possible)


def measure_all(
    fn: Callable[..., Any],
    inputs: Sequence[Sequence[Any]],
    path: Path,
) -> list[BranchCoverage]:
    possible = possible_branch_arcs(path)
    return [
        measure_one(fn, args, path, index, possible=possible)
        for index, args in enumerate(inputs)
    ]


def measure_source(
    source: str,
    entry_point: str,
    inputs: Sequence[Sequence[Any]],
) -> tuple[list[BranchCoverage], frozenset[Arc]]:
    """Measure per-test branch coverage of arbitrary source.

    Returns the per-test observations and the statically possible branch arcs.
    """
    with target_program(source, entry_point) as (path, fn):
        return measure_all(fn, inputs, path), possible_branch_arcs(path)


def union_arcs(observations: Sequence[BranchCoverage]) -> frozenset[Arc]:
    covered: set[Arc] = set()
    for observation in observations:
        covered.update(observation.arcs)
    return frozenset(covered)
