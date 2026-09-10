from __future__ import annotations

from collections.abc import Mapping, Sequence

from .types import TestObservation


def suite_triggers_fault(suite: Sequence[TestObservation]) -> bool:
    return any(t.triggers_fault for t in suite)


def suite_detects_fault(suite: Sequence[TestObservation]) -> bool:
    return any(t.detects_fault for t in suite)


def aggregate_rates(
    suites_by_fault: Mapping[str, Sequence[TestObservation]],
) -> tuple[float, float]:
    """Compute FTR and FDR across faults for one simulation iteration.

    suites_by_fault maps each fault id to the sampled suite selected for that
    fault in this iteration.
    """
    if not suites_by_fault:
        raise ValueError("At least one fault is required")

    n_faults = len(suites_by_fault)
    triggered = sum(suite_triggers_fault(s) for s in suites_by_fault.values())
    detected = sum(suite_detects_fault(s) for s in suites_by_fault.values())
    return triggered / n_faults, detected / n_faults
