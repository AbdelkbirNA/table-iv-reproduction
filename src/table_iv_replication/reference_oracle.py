"""EvalPlus's output oracle, and behavioural comparison of two reference programs.

Why this exists
---------------
HumanEval has two circulating reference implementations per task: the original
dataset's canonical solution (which PromptAnalysis ships verbatim) and EvalPlus's
rewritten one. Fault classification compares a generated program's behaviour
against *a* reference, so which one is used has to be a deliberate, audited
choice. See docs/data_provenance.md.

The comparator
--------------
:func:`compare_outputs` mirrors the per-input oracle inside
``evalplus.eval.unsafe_execute`` (evalplus 0.3.1). It *reuses* EvalPlus's own
``is_floats`` and ``_poly`` rather than reimplementing them, so float, sequence
and special-oracle handling cannot drift from the upstream definition.
"""

from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._output_oracle import compare_outputs
from .target import run_streamed_worker

WORKER = Path(__file__).with_name("_reference_worker.py")

__all__ = [
    "BEHAVIOR_MISMATCH",
    "EQUIVALENT_ON_DOMAIN",
    "INCONCLUSIVE",
    "SOURCE_IDENTICAL",
    "InputVerdict",
    "TaskVerdict",
    "compare_outputs",
    "compare_references",
]

SOURCE_IDENTICAL = "SOURCE_IDENTICAL"
EQUIVALENT_ON_DOMAIN = "SOURCE_DIFFERENT_BEHAVIOR_EQUIVALENT_ON_TEST_DOMAIN"
BEHAVIOR_MISMATCH = "BEHAVIOR_MISMATCH"
INCONCLUSIVE = "EXECUTION_INCONCLUSIVE"


@dataclass(frozen=True)
class InputVerdict:
    """Comparison of the two references on one input."""

    index: int
    agree: bool | None
    reason: str
    kind: str = "value"
    left: str | None = None
    right: str | None = None
    args: str | None = None


@dataclass(frozen=True)
class TaskVerdict:
    """Comparison of the two references over one task's whole input domain."""

    task_id: str
    classification: str
    inputs_tested: int
    source_identical: bool
    mismatches: list[InputVerdict] = field(default_factory=list)
    unobserved: list[InputVerdict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "classification": self.classification,
            "inputs_tested": self.inputs_tested,
            "source_identical": self.source_identical,
            "note": self.note,
            "failures": self.failures,
            "mismatch_kinds": dict(Counter(m.kind for m in self.mismatches)),
            "unobserved_kinds": dict(Counter(m.kind for m in self.unobserved)),
            "unobserved_count": len(self.unobserved),
            "mismatches": [
                {
                    "input_index": m.index,
                    "input": m.args,
                    "left_outcome": m.left,
                    "right_outcome": m.right,
                    "kind": m.kind,
                    "reason": m.reason,
                }
                for m in self.mismatches
            ],
            "unobserved": [
                {
                    "input_index": m.index,
                    "input": m.args,
                    "left_outcome": m.left,
                    "right_outcome": m.right,
                    "kind": m.kind,
                    "reason": m.reason,
                }
                for m in self.unobserved[:20]
            ],
        }


def compare_references(
    task_id: str,
    left_source: str,
    right_source: str,
    entry_point: str,
    inputs: Sequence[Sequence[Any]],
    *,
    atol: float = 0.0,
    dataset: str = "humaneval",
    timeout: float = 1.0,
    timeout_budget: int = 5,
    stall_timeout: float | None = None,
    max_mismatches: int = 50,
) -> TaskVerdict:
    """Run both reference implementations over `inputs` and compare each output.

    Both run in a child process with a per-input timeout, so a pathological
    implementation costs one input rather than the audit. ``left`` is the
    candidate, ``right`` the expectation, matching EvalPlus's asymmetric oracle.
    """
    identical = left_source == right_source
    if identical:
        return TaskVerdict(
            task_id=task_id,
            classification=SOURCE_IDENTICAL,
            inputs_tested=0,
            source_identical=True,
            note="sources are byte-identical; execution skipped",
        )

    work = Path(tempfile.mkdtemp(prefix="table_iv_refeq_"))
    try:
        left_path, right_path = work / "left.py", work / "right.py"
        left_path.write_text(left_source, encoding="utf-8")
        right_path.write_text(right_source, encoding="utf-8")
        plan = {
            "left_path": str(left_path),
            "right_path": str(right_path),
            "entry_point": entry_point,
            "inputs": list(inputs),
            "atol": atol,
            "dataset": dataset,
            "timeout": timeout,
            "timeout_budget": timeout_budget,
        }
        results, worker_failures = run_streamed_worker(
            WORKER,
            plan,
            [(index,) for index in range(len(inputs))],
            work,
            stall_timeout=stall_timeout or max(10.0, timeout * 3),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    failures = [f"input #{key[0]}: {reason}" for key, reason in worker_failures]
    mismatches: list[InputVerdict] = []
    unobserved: list[InputVerdict] = []
    compared = 0

    for index in range(len(inputs)):
        payload = results.get((index,))
        if payload is None:
            unobserved.append(
                InputVerdict(index, None, "worker failure", kind="crash")
            )
            continue
        verdict = InputVerdict(
            index=index,
            agree=payload["agree"],
            reason=payload["reason"],
            kind=payload.get("kind", "value"),
            left=payload.get("left"),
            right=payload.get("right"),
            args=payload.get("args"),
        )
        if payload["agree"] is None:
            unobserved.append(verdict)  # timeout or skipped: not an observation
            continue
        compared += 1
        if not payload["agree"]:
            if len(mismatches) >= max_mismatches:
                verdict = InputVerdict(index, False, payload["reason"], payload.get("kind", "value"))
            mismatches.append(verdict)

    if mismatches:
        classification = BEHAVIOR_MISMATCH
        note = f"{len(mismatches)}/{compared} compared inputs disagree"
    elif unobserved:
        classification = INCONCLUSIVE
        note = (
            f"agree on all {compared} compared inputs, but {len(unobserved)} "
            "input(s) could not be observed"
        )
    else:
        classification = EQUIVALENT_ON_DOMAIN
        note = f"agree on all {compared} inputs of this domain"

    return TaskVerdict(
        task_id=task_id,
        classification=classification,
        inputs_tested=compared,
        source_identical=False,
        mismatches=mismatches,
        unobserved=unobserved,
        failures=failures,
        note=note,
    )
