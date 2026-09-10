import json

import pytest

from table_iv_replication.promptanalysis_adapter import (
    ORIGINAL_GENERATIONS,
    US_DATASET,
    US_GENERATIONS,
    ArtifactMissing,
    GenerationRecord,
    PromptVariant,
    artifact_path,
    load_generations,
    load_prompt_variants,
    manifest,
    read_jsonl,
)

US_RECORD = {
    "task_id": "HumanEval/0",
    "original_prompt": "def f(x):\n    '''full spec'''\n",
    "mutation_type": "US",
    "applicable": True,
    "mutated_prompt": "TASK:\ndef f(x):\n    '''vague'''\n",
}
GENERATION_RECORD = {
    "task_id": "HumanEval/0",
    "prompt": "def f(x):\n",
    "entry_point": "f",
    "canonical_solution": "    return x\n",
    "test": "assert f(1) == 1",
    "GeneratedCode": "def f(x):\n    return x\n",
    "GeneratedResponse": "```python\ndef f(x):\n    return x\n```",
    "PromptUsed": "def f(x):\n",
    "Eval_Status": "OK",
    "TestCases": "assert f(1) == 1",
    "n_Tests": 1,
    "Tests_Passed": 1,
    "Pass@1": True,
}


@pytest.fixture
def fake_dir(tmp_path):
    (tmp_path / US_DATASET).write_text(json.dumps(US_RECORD) + "\n", encoding="utf-8")
    (tmp_path / ORIGINAL_GENERATIONS).write_text(
        json.dumps([GENERATION_RECORD]), encoding="utf-8"
    )
    return tmp_path


def test_prompt_variant_normalizes_field_names_without_losing_anything(fake_dir):
    variant = load_prompt_variants(US_DATASET, fake_dir)[0]

    assert variant.task_id == "HumanEval/0"
    assert variant.mutation_type == "US"
    assert variant.applicable is True
    assert variant.mutated_prompt.startswith("TASK:\n")
    # Fields this schema does not carry stay None, never invented.
    assert variant.entry_point is None
    assert variant.canonical_solution is None
    assert variant.raw == US_RECORD
    assert variant.extra_fields == set()


def test_benchmark_prompt_field_is_read_as_original_prompt(fake_dir):
    record = load_generations(ORIGINAL_GENERATIONS, fake_dir)[0]
    assert record.variant.original_prompt == "def f(x):\n"


def test_generation_record_keeps_unmodeled_fields_in_metadata(fake_dir):
    record = load_generations(ORIGINAL_GENERATIONS, fake_dir)[0]

    assert record.eval_status == "OK"
    assert record.generated_code == "def f(x):\n    return x\n"
    assert record.metadata == GENERATION_RECORD
    assert record.extra_fields == {"TestCases", "n_Tests", "Tests_Passed", "Pass@1", "test"}
    # No generation parameters are inferred when the artifact has none.
    assert "temperature" not in record.metadata
    assert "model" not in record.metadata


def test_malformed_jsonl_lines_stay_visible(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"task_id": "a"}\nnot json\n\n{"task_id": "b"}\n', encoding="utf-8")
    records = read_jsonl(path)

    assert len(records) == 3
    assert records[1]["__malformed__"]
    assert records[1]["__line__"] == 2


def test_missing_artifact_names_the_fetcher(tmp_path):
    with pytest.raises(ArtifactMissing, match="fetch_related_artifacts"):
        artifact_path(US_DATASET, tmp_path)


def test_non_list_generation_payload_is_rejected(tmp_path):
    (tmp_path / ORIGINAL_GENERATIONS).write_text('{"task_id": "x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON list"):
        load_generations(ORIGINAL_GENERATIONS, tmp_path)


# --- against the real fetched artifacts, when present ----------------------

def _fetched():
    try:
        return manifest()
    except ArtifactMissing:
        return None


needs_artifacts = pytest.mark.skipif(_fetched() is None, reason="artifacts not fetched")


@needs_artifacts
def test_artifacts_are_pinned_to_the_audited_commit():
    record = manifest()
    assert record["commit"] == "99f5d447aa55167e177069d90f00f81933002e05"
    assert len(record["artifacts"]) == 4
    assert all(len(entry["sha256"]) == 64 for entry in record["artifacts"].values())


@needs_artifacts
def test_real_artifacts_parse_into_the_expected_shapes():
    variants = load_prompt_variants(US_DATASET)
    generations = load_generations(ORIGINAL_GENERATIONS)
    us_generations = load_generations(US_GENERATIONS)

    assert all(isinstance(v, PromptVariant) for v in variants)
    assert all(isinstance(g, GenerationRecord) for g in generations)
    assert not any("__malformed__" in v.raw for v in variants)
    assert {v.mutation_type for v in variants} == {"US"}
    # One record per task in both generation artifacts -- the multiplicity finding.
    assert len({g.task_id for g in generations}) == len(generations)
    assert len({g.task_id for g in us_generations}) == len(us_generations)
