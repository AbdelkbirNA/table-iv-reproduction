"""Tests for the OpenAI adapter. No network call is ever made: the client is faked."""

import json
import os
from types import SimpleNamespace

import pytest

from table_iv_replication.openai_generation import (
    API_KEY_ENV,
    API_KEY_NOT_CONFIGURED,
    ApiKeyNotConfigured,
    GenerationCall,
    api_key_configured,
    build_client,
    generate,
    redact,
)

FAKE_KEY = "sk-test-NOT-A-REAL-KEY-000000"


class FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))

    @property
    def calls(self):
        return self.chat.completions.calls


def completion(text="```python\ndef test_x():\n    assert True\n```", **overrides):
    data = {
        "id": "chatcmpl-fake-123",
        "model": "gpt-5-mini-2025-08-07",
        "created": 1757500000,
        "system_fingerprint": "fp_fake",
        "choices": [
            SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=text)
            )
        ],
        "usage": SimpleNamespace(
            model_dump=lambda exclude_none=True: {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
                "completion_tokens_details": {"reasoning_tokens": 16},
            }
        ),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, FAKE_KEY)
    return FAKE_KEY


# --- key handling ----------------------------------------------------------

def test_missing_key_is_detected_without_any_call(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    assert api_key_configured() is False
    with pytest.raises(ApiKeyNotConfigured, match=API_KEY_NOT_CONFIGURED):
        build_client()


def test_blank_key_counts_as_not_configured(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "   ")
    assert api_key_configured() is False


def test_key_is_present_but_never_returned(with_key):
    assert api_key_configured() is True
    # The module exposes no accessor for the value itself.
    import table_iv_replication.openai_generation as module

    assert not hasattr(module, "get_api_key")
    assert not hasattr(module, "API_KEY")


def test_the_key_never_reaches_a_serialized_record(with_key):
    client = FakeClient(completion())
    call = generate(
        model="m", system_prompt="s", user_prompt="u", temperature=0.1, client=client
    )
    blob = json.dumps(call.to_dict())

    assert FAKE_KEY not in blob
    assert "api_key" not in blob
    assert "sk-" not in blob


def test_an_error_carrying_the_key_is_redacted_before_being_stored(with_key):
    boom = RuntimeError(f"auth failed for {FAKE_KEY}")
    call = generate(
        model="m", system_prompt="s", user_prompt="u", temperature=0.1,
        client=FakeClient(boom),
    )

    assert call.succeeded is False
    assert FAKE_KEY not in json.dumps(call.to_dict())
    assert "<redacted>" in call.error
    assert redact(f"x {FAKE_KEY} y") == "x <redacted> y"


# --- request construction --------------------------------------------------

def test_request_sends_only_what_was_asked_for(with_key):
    client = FakeClient(completion())
    generate(
        model="gpt-5-mini-2025-08-07",
        system_prompt="sys",
        user_prompt="usr",
        temperature=0.1,
        client=client,
    )
    (sent,) = client.calls

    assert sent["model"] == "gpt-5-mini-2025-08-07"
    assert sent["temperature"] == 0.1
    assert sent["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    # Nothing invented.
    for absent in ("seed", "top_p", "reasoning_effort", "max_tokens",
                   "max_completion_tokens", "n", "stream"):
        assert absent not in sent


def test_temperature_can_be_omitted_entirely(with_key):
    client = FakeClient(completion())
    generate(model="m", system_prompt="s", user_prompt="u", temperature=None,
             client=client)
    assert "temperature" not in client.calls[0]


# --- response parsing ------------------------------------------------------

def test_response_metadata_is_captured(with_key):
    call = generate(
        model="gpt-5-mini-2025-08-07", system_prompt="s", user_prompt="u",
        temperature=0.1, client=FakeClient(completion()),
    )

    assert call.succeeded is True
    assert call.model_returned == "gpt-5-mini-2025-08-07"
    assert call.response_id == "chatcmpl-fake-123"
    assert call.finish_reason == "stop"
    assert call.system_fingerprint == "fp_fake"
    assert call.temperature_requested == 0.1 and call.temperature_sent == 0.1
    assert call.usage["total_tokens"] == 200
    assert call.usage["completion_tokens_details"]["reasoning_tokens"] == 16
    assert call.http_requests_made == 1
    assert call.api_rejected_parameters == ()
    assert call.requested_at and call.completed_at


def test_a_returned_model_different_from_the_request_is_recorded(with_key):
    call = generate(
        model="gpt-5-mini-2025-08-07", system_prompt="s", user_prompt="u",
        temperature=0.1, client=FakeClient(completion(model="gpt-5-mini-other")),
    )
    assert call.model_requested == "gpt-5-mini-2025-08-07"
    assert call.model_returned == "gpt-5-mini-other"


def test_temperature_rejection_is_recorded_and_retried_once_without_it(with_key):
    rejection = RuntimeError(
        "BadRequestError: Unsupported value: 'temperature' does not support 0.1"
    )
    client = FakeClient(rejection, completion())
    call = generate(model="m", system_prompt="s", user_prompt="u", temperature=0.1,
                    client=client)

    assert call.succeeded is True
    assert call.api_rejected_parameters == ("temperature",)
    assert call.temperature_requested == 0.1
    assert call.temperature_sent is None          # what actually went out
    assert call.http_requests_made == 2
    assert "temperature" in call.api_errors[0]
    assert "temperature" in client.calls[0] and "temperature" not in client.calls[1]


def test_a_non_temperature_error_is_not_retried(with_key):
    client = FakeClient(RuntimeError("RateLimitError: slow down"))
    call = generate(model="m", system_prompt="s", user_prompt="u", temperature=0.1,
                    client=client)

    assert call.succeeded is False
    assert call.http_requests_made == 1           # no silent retry
    assert call.api_rejected_parameters == ()
    assert "RateLimitError" in call.error


def test_fallback_can_be_disabled(with_key):
    client = FakeClient(RuntimeError("temperature unsupported"))
    call = generate(model="m", system_prompt="s", user_prompt="u", temperature=0.1,
                    client=client, allow_temperature_fallback=False)
    assert call.succeeded is False
    assert call.http_requests_made == 1


# --- artifact serialization ------------------------------------------------

def test_record_round_trips_through_json(with_key, tmp_path):
    call = generate(model="m", system_prompt="s", user_prompt="u", temperature=0.1,
                    client=FakeClient(completion()))
    path = tmp_path / "raw.json"
    path.write_text(json.dumps(call.to_dict(), indent=2), encoding="utf-8")

    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["response_text"] == call.response_text
    assert restored["usage"]["total_tokens"] == 200
    assert GenerationCall(**{**call.to_dict(),
                            "usage": restored["usage"],
                            "api_rejected_parameters": (),
                            "api_errors": ()}).response_id == "chatcmpl-fake-123"


def test_adapter_module_makes_no_network_call_itself():
    """The SDK is imported lazily inside build_client, never at module import."""
    import pathlib

    import table_iv_replication.openai_generation as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "urllib" not in source
    # The only SDK import is inside build_client.
    assert source.count("from openai import OpenAI") == 1
    assert "    from openai import OpenAI" in source
