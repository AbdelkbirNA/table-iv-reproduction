"""Tests for the pilot driver that do not touch the network."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from conftest import requires_promptanalysis

# Every test here resolves the pilot's candidates out of the fetched
# PromptAnalysis artifacts. Without them there is nothing to drive.
pytestmark = requires_promptanalysis

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

spec = importlib.util.spec_from_file_location(
    "run_llm_plain_pilot", ROOT / "scripts" / "run_llm_plain_pilot.py"
)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_the_three_pilot_candidates_are_fixed():
    assert pilot.PILOT == (
        ("HumanEval/91", "under_specified"),
        ("HumanEval/78", "under_specified"),
        ("HumanEval/151", "original"),
    )
    assert pilot.MODEL == "gpt-5-mini-2025-08-07"
    assert pilot.TEMPERATURE == 0.1


def test_candidates_resolve_and_are_all_faulty_primary():
    candidates = pilot.load_candidates()
    saved = {
        (c["task_id"], c["prompt_variant"]): c
        for c in json.loads(pilot.CLASSIFICATION.read_text())["candidates"]
    }

    assert [c["candidate_id"] for c in candidates] == [
        "HumanEval/91|under_specified",
        "HumanEval/78|under_specified",
        "HumanEval/151|original",
    ]
    for candidate in candidates:
        record = saved[(candidate["task_id"], candidate["prompt_variant"])]
        assert record["primary_classification"] == "FAULTY_PRIMARY"
        assert candidate["faulty_source"].strip()
        assert len(candidate["faulty_sha256"]) == 64
        assert candidate["entry_point"] in candidate["faulty_source"]


def test_faulty_sources_come_from_the_artifacts_unmodified():
    from table_iv_replication.promptanalysis_adapter import load_generations

    candidates = {c["candidate_id"]: c for c in pilot.load_candidates()}
    for variant, artifact in pilot.ARTIFACT_FOR.items():
        records = {g.task_id: g for g in load_generations(artifact)}
        for task_id, wanted in pilot.PILOT:
            if wanted != variant:
                continue
            assert candidates[f"{task_id}|{variant}"]["faulty_source"] == (
                records[task_id].generated_code
            )


def test_missing_api_key_stops_before_any_call(monkeypatch, capsys):
    from table_iv_replication.openai_generation import API_KEY_ENV

    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        pilot, "generate", lambda **_: pytest.fail("no API call may be made")
    )
    exit_code = pilot.main([])

    assert exit_code == 2
    assert "OPENAI_API_KEY_NOT_CONFIGURED" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Candidate-level resume and caching.
#
# The API call is the only part of this pilot that costs money, so it is the
# only part that may ever be repeated by accident. These tests exist to make
# that impossible.
# ---------------------------------------------------------------------------

FAKE_RESPONSE = "```python\ndef test_placeholder():\n    assert True\n```"


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """Isolated raw/output dirs, a key present, and a counting fake generator."""
    monkeypatch.setattr(pilot, "RAW", tmp_path / "raw")
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-NOT-REAL")

    calls = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        return pilot.GenerationCall(
            model_requested=kwargs["model"],
            model_returned=kwargs["model"],
            response_text=FAKE_RESPONSE,
            finish_reason="stop",
            response_id=f"resp_{len(calls)}",
            created=1,
            system_fingerprint="fp_test",
            temperature_requested=kwargs["temperature"],
            temperature_sent=kwargs["temperature"],
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

    monkeypatch.setattr(pilot, "generate", fake_generate)
    return calls


def called_ids(calls, candidates):
    """Which candidates a run actually sent to the API, by prompt content."""
    by_source = {c["faulty_source"]: c["candidate_id"] for c in candidates}
    found = []
    for call in calls:
        for source, candidate_id in by_source.items():
            if source in call["user_prompt"]:
                found.append(candidate_id)
                break
    return found


def test_a_first_run_generates_every_candidate(sandbox):
    candidates = pilot.load_candidates()
    assert pilot.main([]) == 0
    assert len(sandbox) == 3
    assert sorted(called_ids(sandbox, candidates)) == sorted(
        c["candidate_id"] for c in candidates
    )
    for candidate in candidates:
        assert pilot.raw_path(candidate).exists()


def test_completed_candidates_are_skipped_on_rerun(sandbox):
    assert pilot.main([]) == 0
    assert len(sandbox) == 3

    sandbox.clear()
    assert pilot.main([]) == 0
    assert sandbox == [], "a cached candidate must never be sent to the API again"


def test_only_the_missing_candidate_is_generated(sandbox):
    candidates = pilot.load_candidates()
    assert pilot.main([]) == 0
    sandbox.clear()

    # Simulate a run interrupted before the third response was saved.
    missing = candidates[2]
    pilot.raw_path(missing).unlink()

    assert pilot.main([]) == 0
    assert len(sandbox) == 1
    assert called_ids(sandbox, candidates) == [missing["candidate_id"]]


def test_the_cached_response_is_what_gets_reused(sandbox):
    candidates = pilot.load_candidates()
    assert pilot.main([]) == 0
    before = {
        c["candidate_id"]: json.loads(pilot.raw_path(c).read_text())["api"]
        for c in candidates
    }

    sandbox.clear()
    assert pilot.main([]) == 0

    for candidate in candidates:
        record = json.loads(pilot.raw_path(candidate).read_text())
        # The paid part is preserved byte for byte, response id included.
        assert record["api"] == before[candidate["candidate_id"]]
        assert record["reused_from_cache"] is True


def test_force_regenerates_everything(sandbox):
    candidates = pilot.load_candidates()
    assert pilot.main([]) == 0
    sandbox.clear()

    assert pilot.main(["--force"]) == 0
    assert len(sandbox) == 3
    assert sorted(called_ids(sandbox, candidates)) == sorted(
        c["candidate_id"] for c in candidates
    )
    for candidate in candidates:
        assert json.loads(pilot.raw_path(candidate).read_text())["reused_from_cache"] is False


def test_a_fully_cached_rerun_needs_no_api_key(sandbox, monkeypatch):
    from table_iv_replication.openai_generation import API_KEY_ENV

    assert pilot.main([]) == 0
    sandbox.clear()

    # Everything is cached, so the run must complete offline.
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        pilot, "generate", lambda **_: pytest.fail("no API call may be made")
    )
    assert pilot.main([]) == 0


def test_a_partly_cached_run_without_a_key_names_what_is_missing(sandbox, monkeypatch, capsys):
    from table_iv_replication.openai_generation import API_KEY_ENV

    candidates = pilot.load_candidates()
    assert pilot.main([]) == 0
    pilot.raw_path(candidates[1]).unlink()

    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        pilot, "generate", lambda **_: pytest.fail("no API call may be made")
    )
    assert pilot.main([]) == 2
    output = capsys.readouterr().out
    assert candidates[1]["candidate_id"] in output
    assert "OPENAI_API_KEY_NOT_CONFIGURED" in output


def test_a_failed_call_is_not_treated_as_a_cache_hit(sandbox, monkeypatch):
    candidates = pilot.load_candidates()
    failed = dict(
        candidate_id=candidates[0]["candidate_id"],
        api=pilot.GenerationCall(
            model_requested=pilot.MODEL,
            model_returned=None,
            response_text=None,          # the call never produced anything
            finish_reason=None,
            response_id=None,
            created=None,
            system_fingerprint=None,
            temperature_requested=pilot.TEMPERATURE,
            temperature_sent=pilot.TEMPERATURE,
            error="RateLimitError: slow down",
        ).to_dict(),
    )
    pilot.RAW.mkdir(parents=True, exist_ok=True)
    pilot.write_record(pilot.raw_path(candidates[0]), failed)

    assert pilot.main([]) == 0
    assert candidates[0]["candidate_id"] in called_ids(sandbox, candidates)


def test_an_unusable_file_is_kept_aside_not_destroyed(sandbox):
    candidates = pilot.load_candidates()
    path = pilot.raw_path(candidates[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"api": {"response_text": "truncat', encoding="utf-8")

    assert pilot.main([]) == 0

    kept = list(path.parent.glob(f"{path.stem}.unusable-*.json"))
    assert len(kept) == 1, "a corrupt response must be preserved, never silently dropped"
    assert kept[0].read_text().startswith('{"api"')
    assert json.loads(path.read_text())["api"]["response_text"] == FAKE_RESPONSE


def test_writes_are_atomic_and_leave_no_temporary_files(sandbox):
    assert pilot.main([]) == 0
    assert list(pilot.RAW.glob("*.tmp")) == []
    assert list(pilot.OUT.glob("*.tmp")) == []


def test_the_manifest_separates_billed_requests_from_reuse(sandbox):
    assert pilot.main([]) == 0
    first = json.loads((pilot.OUT / "manifest.json").read_text())
    assert first["api_requests_billed_this_run"] == 3
    assert first["reused_from_cache"] == 0

    sandbox.clear()
    assert pilot.main([]) == 0
    second = json.loads((pilot.OUT / "manifest.json").read_text())
    assert second["api_requests_billed_this_run"] == 0
    assert second["reused_from_cache"] == 3


def test_the_generation_protocol_is_unchanged_by_caching(sandbox):
    """Caching must not touch what is sent: model, temperature, prompts."""
    assert pilot.main([]) == 0
    for call in sandbox:
        assert call["model"] == pilot.MODEL
        assert call["temperature"] == pilot.TEMPERATURE
        assert call["system_prompt"] == "You are a tool used by Python Developers to generate tests."
        assert "100% code coverage" in call["user_prompt"]
        assert set(call) == {"model", "system_prompt", "user_prompt", "temperature"}


def test_round_tripping_a_call_through_disk_preserves_it(sandbox):
    original = pilot.GenerationCall(
        model_requested="m", model_returned="m-1", response_text="x",
        finish_reason="stop", response_id="id", created=7, system_fingerprint="fp",
        temperature_requested=0.1, temperature_sent=None,
        usage={"total_tokens": 5}, http_requests_made=2,
        api_rejected_parameters=("temperature",), api_errors=("boom",),
    )
    assert pilot.GenerationCall.from_dict(json.loads(json.dumps(original.to_dict()))) == original


@pytest.mark.parametrize(
    "code, expected",
    [
        ("def test_a():\n    has_close(1)\n", "direct_call"),
        ("from solution import has_close\n\ndef test_a():\n    has_close(1)\n", "import_solution"),
        ("import implementation\n\ndef test_a():\n    pass\n", "import_implementation"),
        ("from module_under_test import has_close\n", "import_module_under_test"),
        ("from mycode import has_close\n", "import_other"),
        ("def has_close(x):\n    return x\n", "redefines_target"),
        ("def test_a():\n    assert 1 == 1\n", "none"),
    ],
)
def test_access_pattern_classification(code, expected):
    pattern, _ = pilot.classify_access(code, "has_close")
    assert pattern == expected


def test_dry_run_needs_no_api_key_and_makes_no_call(monkeypatch, capsys):
    from table_iv_replication.openai_generation import API_KEY_ENV

    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        pilot, "generate", lambda **_: pytest.fail("no API call may be made")
    )
    assert pilot.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "No API call was made (--dry-run)." in output
    assert "OPENAI_API_KEY_NOT_CONFIGURED" not in output
