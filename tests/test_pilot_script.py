"""Tests for the pilot driver that do not touch the network."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


def test_existing_raw_files_stop_the_run_without_force(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pilot, "RAW", tmp_path)
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-NOT-REAL")
    monkeypatch.setattr(
        pilot, "generate", lambda **_: pytest.fail("no API call may be made")
    )
    for candidate in pilot.load_candidates():
        pilot.raw_path(candidate).write_text("{}", encoding="utf-8")

    assert pilot.main([]) == 3
    assert "already exist" in capsys.readouterr().out


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
