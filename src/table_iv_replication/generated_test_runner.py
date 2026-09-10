"""Run LLM-generated tests against a reference/faulty pair; separate FTR from FDR.

Two facts are computed per generated test, and never collapsed:

* **triggered** -- at least one recorded invocation of the entry point produces a
  different observable outcome on the faulty program than on the reference, under
  the same comparator used everywhere else in this project. This feeds FTR.
* **detected** -- the test's *own* oracle flags the faulty behaviour: it passes
  on the reference and its assertion fails on the faulty program. This feeds FDR.

A test can trigger without detecting (`assert foo(1) in (10, 11)`), and an oracle
that agrees with the faulty behaviour (`assert foo(1) == 7` when the reference
returns 5) triggers and does *not* detect -- it is recorded as
FAULTY_BIASED_ORACLE, the effect the paper's RQ3 examines.

Inputs are captured by runtime instrumentation, not by parsing values out of the
source: the entry point is proxied and every call recorded, so a test calling it
five times with unparseable expressions is handled exactly like a literal one.

Detection is deliberately conservative. Broken generated tests, imports that fail,
timeouts and fault-induced crashes never count as detection; they are classified
explicitly so FDR is not inflated.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .llm_plain_protocol import TestCaseRecord
from .target import run_streamed_worker
from .types import TestObservation

WORKER = Path(__file__).with_name("_generated_test_worker.py")

#: Import names under which the program under test is exposed to a generated
#: test, in addition to the entry point being present as a global.
DEFAULT_MODULE_ALIASES = ("solution", "implementation", "module_under_test")


class TestStatus(str, Enum):
    """Outcome of executing one generated test against one program."""

    PASS = "PASS"
    ASSERTION_FAILURE = "ASSERTION_FAILURE"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    INVALID_TEST = "INVALID_TEST"


# Not a pytest test class despite the name.
TestStatus.__test__ = False


class DetectionVerdict(str, Enum):
    """How the test's oracle behaved across the reference/faulty pair."""

    #: Passes on the reference, its assertion fails on the faulty program, and an
    #: input genuinely distinguishes the two. The only verdict counting for FDR.
    DETECTED = "DETECTED"
    #: An input distinguishes the programs but the oracle is indifferent to it.
    TRIGGERED_NOT_DETECTED = "TRIGGERED_NOT_DETECTED"
    #: No recorded input distinguishes the programs.
    NOT_TRIGGERED = "NOT_TRIGGERED"
    #: The assertion fails on the reference too: the oracle is simply wrong.
    INVALID_ORACLE = "INVALID_ORACLE"
    #: Fails on the reference, passes on the faulty program -- the oracle encodes
    #: the faulty behaviour. Scientifically the interesting one.
    FAULTY_BIASED_ORACLE = "FAULTY_BIASED_ORACLE"
    #: The test could not be executed meaningfully on the reference at all.
    BROKEN_TEST = "BROKEN_TEST"
    #: Something happened that is not attributable to the oracle -- e.g. the
    #: faulty program crashed, or an assertion failed with no distinguishing
    #: input recorded. Never counted as detection.
    DETECTION_INCONCLUSIVE = "DETECTION_INCONCLUSIVE"


@dataclass(frozen=True)
class Invocation:
    """One recorded call of the entry point made by the test."""

    order: int
    args: str
    kwargs: str
    outcome: tuple[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "args": self.args,
            "kwargs": self.kwargs,
            "outcome": list(self.outcome),
        }


@dataclass(frozen=True)
class ProgramRun:
    """The test's behaviour against one of the two programs."""

    status: TestStatus
    error: str | None = None
    invocations: tuple[Invocation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "error": self.error,
            "invocations": [call.to_dict() for call in self.invocations],
        }


@dataclass(frozen=True)
class GeneratedTestResult:
    """Everything one generated test tells us about one fault."""

    __test__ = False

    test_name: str
    fault_id: str
    reference_run: ProgramRun
    faulty_run: ProgramRun
    triggered: bool
    verdict: DetectionVerdict
    note: str = ""
    replay: tuple[dict[str, Any], ...] = ()
    distinct_inputs: int = 0
    unobserved_inputs: int = 0
    module_level: bool = False

    @property
    def triggers_fault(self) -> bool:
        return self.triggered

    @property
    def detects_fault(self) -> bool:
        """Only DETECTED counts. A crash or a broken test never does."""
        return self.verdict is DetectionVerdict.DETECTED

    @property
    def invocation_count(self) -> int:
        return len(self.reference_run.invocations) + len(self.faulty_run.invocations)

    @property
    def triggering_inputs(self) -> list[dict[str, Any]]:
        return [item for item in self.replay if item.get("differs")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "fault_id": self.fault_id,
            "module_level": self.module_level,
            "triggered": self.triggered,
            "detected": self.detects_fault,
            "verdict": self.verdict.value,
            "note": self.note,
            "reference_run": self.reference_run.to_dict(),
            "faulty_run": self.faulty_run.to_dict(),
            "distinct_inputs": self.distinct_inputs,
            "unobserved_inputs": self.unobserved_inputs,
            "replay": list(self.replay),
        }


def classify_detection(
    reference: TestStatus, faulty: TestStatus, triggered: bool
) -> tuple[DetectionVerdict, str]:
    """Decide the verdict from the two statuses and the trigger fact.

    Order matters: a test that cannot run on the reference tells us nothing about
    the oracle, so that is checked before anything else.
    """
    V = DetectionVerdict

    if TestStatus.TIMEOUT in (reference, faulty):
        return V.BROKEN_TEST, f"timed out (reference={reference.value}, faulty={faulty.value})"
    if reference is TestStatus.INVALID_TEST or faulty is TestStatus.INVALID_TEST:
        return V.BROKEN_TEST, "the generated test could not be executed"
    if reference is TestStatus.RUNTIME_ERROR:
        return V.BROKEN_TEST, "the test errors on the reference, so its oracle is untrustworthy"

    if reference is TestStatus.ASSERTION_FAILURE:
        if faulty is TestStatus.PASS:
            return V.FAULTY_BIASED_ORACLE, (
                "the oracle fails on the reference and passes on the faulty program: "
                "it encodes the faulty behaviour"
            )
        return V.INVALID_ORACLE, "the assertion fails on the reference too"

    # reference PASS from here on.
    if faulty is TestStatus.ASSERTION_FAILURE:
        if triggered:
            return V.DETECTED, "passes on the reference, its assertion fails on the faulty program"
        return V.DETECTION_INCONCLUSIVE, (
            "the assertion fails on the faulty program but no recorded input "
            "distinguishes the two programs"
        )
    if faulty is TestStatus.RUNTIME_ERROR:
        return V.DETECTION_INCONCLUSIVE, (
            "the faulty program raised rather than the oracle judging it; "
            "not counted as detection"
        )
    return (
        (V.TRIGGERED_NOT_DETECTED, "an input distinguishes the programs but the oracle accepts both")
        if triggered
        else (V.NOT_TRIGGERED, "no recorded input distinguishes the programs")
    )


def run_generated_tests(
    reference_source: str,
    faulty_source: str,
    entry_point: str,
    tests: Sequence[TestCaseRecord],
    *,
    fault_id: str = "",
    atol: float = 0.0,
    dataset: str = "humaneval",
    timeout: float = 2.0,
    stall_timeout: float | None = None,
    module_aliases: Sequence[str] = DEFAULT_MODULE_ALIASES,
) -> list[GeneratedTestResult]:
    """Execute each generated test against both programs in an isolated child.

    Nothing is executed in this process. One child handles the whole batch,
    streaming a result per test; if a test wedges the child, the parent kills it,
    records that test as timed out, and continues with the rest.
    """
    if not tests:
        return []

    work = Path(tempfile.mkdtemp(prefix="table_iv_gentests_"))
    try:
        reference_path = work / "reference.py"
        faulty_path = work / "faulty.py"
        reference_path.write_text(reference_source, encoding="utf-8")
        faulty_path.write_text(faulty_source, encoding="utf-8")

        plan = {
            "reference_path": str(reference_path),
            "faulty_path": str(faulty_path),
            "entry_point": entry_point,
            "atol": atol,
            "dataset": dataset,
            "timeout": timeout,
            "module_aliases": tuple(module_aliases),
            "tests": [
                {
                    "name": test.name,
                    "module_source": test.runnable_module,
                    "module_level": test.module_level,
                }
                for test in tests
            ],
        }
        results, failures = run_streamed_worker(
            WORKER,
            plan,
            [(index,) for index in range(len(tests))],
            work,
            # Two program runs plus a replay per test, so allow a wider stall
            # window than a single call would need.
            stall_timeout=stall_timeout or max(15.0, timeout * 8),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    failed = {key[0]: reason for key, reason in failures}
    output: list[GeneratedTestResult] = []
    for index, test in enumerate(tests):
        payload = results.get((index,))
        if payload is None:
            reason = failed.get(index, "worker produced no result")
            broken = ProgramRun(TestStatus.TIMEOUT, reason)
            output.append(
                GeneratedTestResult(
                    test_name=test.name,
                    fault_id=fault_id,
                    reference_run=broken,
                    faulty_run=broken,
                    triggered=False,
                    verdict=DetectionVerdict.BROKEN_TEST,
                    note=f"not executed: {reason}",
                    module_level=test.module_level,
                )
            )
            continue

        runs = {}
        for side in ("reference", "faulty"):
            data = payload[side]
            runs[side] = ProgramRun(
                status=TestStatus(data["status"]),
                error=data["error"],
                invocations=tuple(
                    Invocation(
                        order=call["order"],
                        args=call["args"],
                        kwargs=call["kwargs"],
                        outcome=(call["outcome"][0], call["outcome"][1]),
                    )
                    for call in data["invocations"]
                ),
            )
        triggered = bool(payload["triggered"])
        verdict, note = classify_detection(
            runs["reference"].status, runs["faulty"].status, triggered
        )
        output.append(
            GeneratedTestResult(
                test_name=test.name,
                fault_id=fault_id,
                reference_run=runs["reference"],
                faulty_run=runs["faulty"],
                triggered=triggered,
                verdict=verdict,
                note=note,
                replay=tuple(payload["replay"]),
                distinct_inputs=payload["distinct_inputs"],
                unobserved_inputs=payload["unobserved_inputs"],
                module_level=test.module_level,
            )
        )
    return output


def to_test_observation(
    result: GeneratedTestResult,
    adequacy_items: frozenset[str] | Sequence[str],
) -> TestObservation:
    """Bridge a behavioural result into the sampler's input.

    Adequacy is *not* computed here. The caller supplies the criterion items --
    statement lines, branch arcs or killed mutant ids -- so the same generated
    test feeds all three criteria without re-running it.
    """
    return TestObservation(
        test_id=f"{result.fault_id}|{result.test_name}" if result.fault_id else result.test_name,
        adequacy_items=frozenset(adequacy_items),
        triggers_fault=result.triggers_fault,
        detects_fault=result.detects_fault,
    )


def summarize_results(results: Sequence[GeneratedTestResult]) -> dict[str, Any]:
    """Counts by verdict, plus suite-level trigger/detection facts."""
    from collections import Counter

    verdicts = Counter(result.verdict.value for result in results)
    return {
        "tests": len(results),
        "triggering": sum(1 for result in results if result.triggers_fault),
        "detecting": sum(1 for result in results if result.detects_fault),
        "suite_triggers_fault": any(result.triggers_fault for result in results),
        "suite_detects_fault": any(result.detects_fault for result in results),
        "verdicts": dict(verdicts),
    }
