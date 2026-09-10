"""Label generated implementations faulty or not against a reference program.

Decision A4 (docs/experiment_plan.md): the **primary oracle** is the EvalPlus
0.3.1 HumanEval+ canonical implementation. The original HumanEval canonical
solution is kept as a **secondary sensitivity oracle**, because the two disagree
on 17 tasks (docs/data_provenance.md section E).

A candidate is faulty when at least one input in the domain produces an
observable outcome differing from the reference's, under the same comparator
used by the reference-equivalence audit
(``_output_oracle.compare_outputs``, mirroring evalplus 0.3.1). A timeout on
either side is *unobserved*, never a trigger.

Generated code is untrusted and runs only in ``_classification_worker.py``.
"""

from __future__ import annotations

import shutil
import statistics
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .target import run_streamed_worker

WORKER = Path(__file__).with_name("_classification_worker.py")

PROMPT_ORIGINAL = "original"
PROMPT_US = "under_specified"

REF_EVALPLUS = "ref_evalplus"
REF_ORIGINAL = "ref_original"

FAULTY = "FAULTY_PRIMARY"
CORRECT = "CORRECT_ON_OBSERVED_DOMAIN"
INCONCLUSIVE = "INCONCLUSIVE"
UNUSABLE = "UNUSABLE_GENERATED_CODE"

FAULTY_SECONDARY = "FAULTY_ORIGINAL_REFERENCE"
CORRECT_SECONDARY = "CORRECT_ORIGINAL_REFERENCE"

SENS_FAULTY_BOTH = "faulty_under_both"
SENS_CORRECT_BOTH = "correct_under_both"
SENS_EVALPLUS_ONLY = "faulty_only_under_evalplus"
SENS_ORIGINAL_ONLY = "faulty_only_under_original_humaneval"
SENS_INCONCLUSIVE = "sensitivity_inconclusive"

# The paper discards faults with difficulty below 0.75, i.e. retains those at
# 0.75 or above -- faults triggered by at most 25% of the tests. Inclusive.
DIFFICULTY_THRESHOLD = 0.75


@dataclass(frozen=True)
class Candidate:
    """One generated implementation to be labelled."""

    candidate_id: str
    task_id: str
    prompt_variant: str
    artifact: str
    source: str | None

    @property
    def slot(self) -> str:
        return f"cand_{self.prompt_variant}"

    @property
    def has_code(self) -> bool:
        return bool((self.source or "").strip())


@dataclass(frozen=True)
class CandidateResult:
    """Outcome of labelling one candidate over one task's input domain."""

    candidate_id: str
    task_id: str
    prompt_variant: str
    artifact: str
    generated_code_present: bool
    loadable: bool
    primary_classification: str
    secondary_classification: str
    sensitivity: str
    total_inputs: int = 0
    observed_inputs: int = 0
    triggered_inputs: int = 0
    unobserved_inputs: int = 0
    secondary_observed_inputs: int = 0
    secondary_triggered_inputs: int = 0
    first_triggering_indices: list[int] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def trigger_ratio(self) -> float | None:
        if not self.observed_inputs:
            return None
        return self.triggered_inputs / self.observed_inputs

    @property
    def evalplus_domain_difficulty(self) -> float | None:
        """1 - trigger_ratio over the EvalPlus input domain.

        NOT the paper's fault difficulty: the paper measures it over the
        fault_discovery_augmented_tests suite, which we do not have. That suite
        is also distinct from the table_iv_llm_plain_tests pool Table IV samples
        from. See "Two distinct LLM-generated test processes" in
        docs/experiment_plan.md.
        """
        ratio = self.trigger_ratio
        return None if ratio is None else 1.0 - ratio

    @property
    def provisional_difficult_candidate(self) -> bool:
        difficulty = self.evalplus_domain_difficulty
        return (
            self.primary_classification == FAULTY
            and difficulty is not None
            and difficulty >= DIFFICULTY_THRESHOLD
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "task_id": self.task_id,
            "prompt_variant": self.prompt_variant,
            "source_artifact": self.artifact,
            "generated_code_present": self.generated_code_present,
            "loadable": self.loadable,
            "primary_classification": self.primary_classification,
            "secondary_classification": self.secondary_classification,
            "reference_sensitivity": self.sensitivity,
            "total_inputs": self.total_inputs,
            "observed_inputs": self.observed_inputs,
            "unobserved_inputs": self.unobserved_inputs,
            "triggered_inputs": self.triggered_inputs,
            "trigger_ratio": self.trigger_ratio,
            "evalplus_domain_difficulty": self.evalplus_domain_difficulty,
            "provisional_difficult_candidate": self.provisional_difficult_candidate,
            "secondary_observed_inputs": self.secondary_observed_inputs,
            "secondary_triggered_inputs": self.secondary_triggered_inputs,
            "first_triggering_indices": self.first_triggering_indices,
            "examples": self.examples,
            "diagnostics": self.diagnostics,
        }


def _label(triggered: int, observed: int, unobserved: int, available: bool) -> str:
    if not available:
        return INCONCLUSIVE
    if triggered:
        return FAULTY  # one observed difference is proof
    if not observed:
        return INCONCLUSIVE
    if unobserved:
        # Cannot claim correctness over a domain we did not fully observe.
        return INCONCLUSIVE
    return CORRECT


def _sensitivity(primary: str, secondary: str) -> str:
    if primary == INCONCLUSIVE or secondary == INCONCLUSIVE:
        return SENS_INCONCLUSIVE
    primary_faulty = primary == FAULTY
    secondary_faulty = secondary == FAULTY_SECONDARY
    if primary_faulty and secondary_faulty:
        return SENS_FAULTY_BOTH
    if not primary_faulty and not secondary_faulty:
        return SENS_CORRECT_BOTH
    return SENS_EVALPLUS_ONLY if primary_faulty else SENS_ORIGINAL_ONLY


def classify_task(
    task_id: str,
    entry_point: str,
    inputs: Sequence[Sequence[Any]],
    candidates: Sequence[Candidate],
    *,
    evalplus_reference: str,
    original_reference: str | None = None,
    atol: float = 0.0,
    dataset: str = "humaneval",
    timeout: float = 1.0,
    timeout_budget: int = 5,
    stall_timeout: float | None = None,
    max_trigger_indices: int = 20,
) -> list[CandidateResult]:
    """Label every candidate for one task, in a single isolated child process.

    Both references and both candidates run over the same input in the same
    process, so reference outcomes are computed once and shared rather than
    recomputed per candidate.
    """
    usable = [c for c in candidates if c.has_code]
    blank = [
        CandidateResult(
            candidate_id=c.candidate_id,
            task_id=c.task_id,
            prompt_variant=c.prompt_variant,
            artifact=c.artifact,
            generated_code_present=False,
            loadable=False,
            primary_classification=UNUSABLE,
            secondary_classification=UNUSABLE,
            sensitivity=SENS_INCONCLUSIVE,
            total_inputs=len(inputs),
            diagnostics={"reason": "GeneratedCode is empty or blank"},
        )
        for c in candidates
        if not c.has_code
    ]
    if not usable:
        return blank

    work = Path(tempfile.mkdtemp(prefix="table_iv_faults_"))
    try:
        programs: dict[str, str] = {}
        path = work / f"{REF_EVALPLUS}.py"
        path.write_text(evalplus_reference, encoding="utf-8")
        programs[REF_EVALPLUS] = str(path)
        if original_reference is not None:
            path = work / f"{REF_ORIGINAL}.py"
            path.write_text(original_reference, encoding="utf-8")
            programs[REF_ORIGINAL] = str(path)
        for candidate in usable:
            path = work / f"{candidate.slot}.py"
            path.write_text(candidate.source, encoding="utf-8")
            programs[candidate.slot] = str(path)

        references = [name for name in (REF_EVALPLUS, REF_ORIGINAL) if name in programs]
        plan = {
            "entry_point": entry_point,
            "inputs": list(inputs),
            "atol": atol,
            "dataset": dataset,
            "timeout": timeout,
            "timeout_budget": timeout_budget,
            "programs": programs,
            "references": references,
            "candidates": [c.slot for c in usable],
        }
        results, worker_failures = run_streamed_worker(
            WORKER,
            plan,
            [(index,) for index in range(len(inputs))],
            work,
            stall_timeout=stall_timeout or max(10.0, timeout * 6),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    load_errors = results.get(("__load__",)) or {}
    tallies: dict[str, dict[str, Any]] = {
        candidate.slot: {
            REF_EVALPLUS: {"agree": 0, "differ": 0, "unobserved": 0, "unavailable": 0},
            REF_ORIGINAL: {"agree": 0, "differ": 0, "unobserved": 0, "unavailable": 0},
            "indices": [],
            "examples": {},
        }
        for candidate in usable
    }
    crashed_inputs = 0
    for index in range(len(inputs)):
        payload = results.get((index,))
        if payload is None:
            crashed_inputs += 1
            for tally in tallies.values():
                tally[REF_EVALPLUS]["unobserved"] += 1
                tally[REF_ORIGINAL]["unobserved"] += 1
            continue
        for slot, per_reference in payload["verdicts"].items():
            tally = tallies[slot]
            for reference, verdict in per_reference.items():
                tally[reference][verdict] += 1
                if reference == REF_EVALPLUS and verdict == "differ":
                    if len(tally["indices"]) < max_trigger_indices:
                        tally["indices"].append(index)
        for key, example in payload.get("examples", {}).items():
            slot, _, reference = key.partition("|")
            if slot in tallies:
                tallies[slot]["examples"].setdefault(reference, []).append(example)

    output = list(blank)
    for candidate in usable:
        tally = tallies[candidate.slot]
        loadable = candidate.slot not in load_errors
        primary_counts = tally[REF_EVALPLUS]
        secondary_counts = tally[REF_ORIGINAL]

        observed = primary_counts["agree"] + primary_counts["differ"]
        unobserved = primary_counts["unobserved"] + primary_counts["unavailable"]
        primary = _label(primary_counts["differ"], observed, unobserved, loadable)

        secondary_observed = secondary_counts["agree"] + secondary_counts["differ"]
        secondary_unobserved = secondary_counts["unobserved"] + secondary_counts["unavailable"]
        secondary_available = loadable and REF_ORIGINAL not in load_errors and original_reference is not None
        secondary_raw = _label(
            secondary_counts["differ"], secondary_observed, secondary_unobserved, secondary_available
        )
        secondary = {
            FAULTY: FAULTY_SECONDARY,
            CORRECT: CORRECT_SECONDARY,
            INCONCLUSIVE: INCONCLUSIVE,
        }[secondary_raw]

        diagnostics: dict[str, Any] = {
            "agree": primary_counts["agree"],
            "unobserved_or_timeout": primary_counts["unobserved"],
            "worker_crashed_inputs": crashed_inputs,
            "worker_failures": len(worker_failures),
        }
        if not loadable:
            diagnostics["load_error"] = load_errors[candidate.slot]
        if REF_ORIGINAL in load_errors:
            diagnostics["original_reference_load_error"] = load_errors[REF_ORIGINAL]

        output.append(
            CandidateResult(
                candidate_id=candidate.candidate_id,
                task_id=candidate.task_id,
                prompt_variant=candidate.prompt_variant,
                artifact=candidate.artifact,
                generated_code_present=True,
                loadable=loadable,
                primary_classification=primary if loadable else UNUSABLE,
                secondary_classification=secondary if loadable else UNUSABLE,
                sensitivity=_sensitivity(primary, secondary) if loadable else SENS_INCONCLUSIVE,
                total_inputs=len(inputs),
                observed_inputs=observed,
                triggered_inputs=primary_counts["differ"],
                unobserved_inputs=unobserved,
                secondary_observed_inputs=secondary_observed,
                secondary_triggered_inputs=secondary_counts["differ"],
                first_triggering_indices=tally["indices"],
                examples=tally["examples"].get(REF_EVALPLUS, []),
                diagnostics=diagnostics,
            )
        )
    return output


def summarize(results: Sequence[CandidateResult]) -> dict[str, Any]:
    """Counts and difficulty statistics for one group of candidates."""
    faulty = [r for r in results if r.primary_classification == FAULTY]
    difficulties = [
        r.evalplus_domain_difficulty
        for r in faulty
        if r.evalplus_domain_difficulty is not None
    ]
    summary: dict[str, Any] = {
        "total_records": len(results),
        "generated_code_present": sum(1 for r in results if r.generated_code_present),
        "executable_records": sum(1 for r in results if r.loadable),
        "unusable": sum(1 for r in results if r.primary_classification == UNUSABLE),
        "faulty_primary": len(faulty),
        "correct_on_observed_domain": sum(
            1 for r in results if r.primary_classification == CORRECT
        ),
        "inconclusive": sum(1 for r in results if r.primary_classification == INCONCLUSIVE),
        "provisional_difficult_candidates": sum(
            1 for r in results if r.provisional_difficult_candidate
        ),
    }
    if difficulties:
        summary["difficulty"] = {
            "mean": statistics.fmean(difficulties),
            "median": statistics.median(difficulties),
            "min": min(difficulties),
            "max": max(difficulties),
            "n": len(difficulties),
        }
    else:
        summary["difficulty"] = None
    return summary
