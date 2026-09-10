"""Readers for the PromptAnalysis artifacts.

PromptAnalysis is the replication package of *related work* cited by the Table
IV paper, not the Table IV replication package itself. See
docs/data_provenance.md before drawing any conclusion from these records.

This module normalizes field names and nothing else. Every field the artifact
carries is preserved verbatim on ``raw`` / ``metadata``, so an audit can see
what is actually there rather than what we hoped would be there. No filtering,
no defaulting of missing generation parameters, no research assumptions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "external" / "promptanalysis"
MANIFEST = DATA_DIR / "manifest.json"

US_DATASET = "HumanEval_US_mutated.jsonl"
BENCHMARK_DATASET = "HumanEval.jsonl"
ORIGINAL_GENERATIONS = "gpt-5-mini__HumanEval.json"
US_GENERATIONS = "gpt-5-mini__HumanEval_US_with_tests.json"

_VARIANT_FIELDS = {
    "task_id",
    "original_prompt",
    "prompt",
    "mutated_prompt",
    "mutation_type",
    "applicable",
    "entry_point",
    "canonical_solution",
}
_GENERATION_FIELDS = {
    "task_id",
    "PromptUsed",
    "GeneratedCode",
    "GeneratedResponse",
    "Eval_Status",
}


class ArtifactMissing(FileNotFoundError):
    """Raised when an artifact has not been fetched yet."""


@dataclass(frozen=True)
class PromptVariant:
    """One task's prompt material, as stored by the artifact."""

    task_id: str
    original_prompt: str | None = None
    mutated_prompt: str | None = None
    mutation_type: str | None = None
    applicable: bool | None = None
    entry_point: str | None = None
    canonical_solution: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "PromptVariant":
        return cls(
            task_id=record.get("task_id"),
            # HumanEval.jsonl calls it "prompt"; the mutated dataset "original_prompt".
            original_prompt=record.get("original_prompt", record.get("prompt")),
            mutated_prompt=record.get("mutated_prompt"),
            mutation_type=record.get("mutation_type"),
            applicable=record.get("applicable"),
            entry_point=record.get("entry_point"),
            canonical_solution=record.get("canonical_solution"),
            raw=dict(record),
        )

    @property
    def extra_fields(self) -> set[str]:
        """Artifact fields this dataclass does not model."""
        return set(self.raw) - _VARIANT_FIELDS


@dataclass(frozen=True)
class GenerationRecord:
    """One model generation for one task, as stored by the artifact."""

    task_id: str
    prompt_used: str | None = None
    generated_code: str | None = None
    generated_response: str | None = None
    eval_status: str | None = None
    variant: PromptVariant | None = None
    metadata: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "GenerationRecord":
        return cls(
            task_id=record.get("task_id"),
            prompt_used=record.get("PromptUsed"),
            generated_code=record.get("GeneratedCode"),
            generated_response=record.get("GeneratedResponse"),
            eval_status=record.get("Eval_Status"),
            variant=PromptVariant.from_record(record),
            metadata=dict(record),
        )

    @property
    def extra_fields(self) -> set[str]:
        """Artifact fields modeled by neither this dataclass nor PromptVariant."""
        return set(self.metadata) - _GENERATION_FIELDS - _VARIANT_FIELDS


def artifact_path(name: str, data_dir: Path | None = None) -> Path:
    path = (data_dir or DATA_DIR) / name
    if not path.exists():
        raise ArtifactMissing(
            f"{path} is missing; run scripts/fetch_related_artifacts.py first"
        )
    return path


def manifest(data_dir: Path | None = None) -> dict[str, Any]:
    """Provenance record written by the fetcher (repo, commit, checksums)."""
    return json.loads(artifact_path("manifest.json", data_dir).read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file, keeping malformed lines visible rather than dropping them."""
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            records.append({"__malformed__": str(exc), "__line__": number})
    return records


def load_prompt_variants(name: str = US_DATASET, data_dir: Path | None = None) -> list[PromptVariant]:
    return [
        PromptVariant.from_record(record)
        for record in read_jsonl(artifact_path(name, data_dir))
    ]


def load_generations(
    name: str = ORIGINAL_GENERATIONS, data_dir: Path | None = None
) -> list[GenerationRecord]:
    payload = json.loads(artifact_path(name, data_dir).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{name}: expected a JSON list, got {type(payload).__name__}")
    return [GenerationRecord.from_record(record) for record in payload]
