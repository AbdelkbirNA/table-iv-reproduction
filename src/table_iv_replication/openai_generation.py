"""Minimal, replaceable OpenAI adapter for the LLM-Plain reconstruction pilot.

Deliberately thin: one function that sends a system prompt and a user prompt and
returns a structured record. Swapping providers means replacing `generate` and
nothing else.

**Credentials are never handled here beyond checking that the environment
variable exists.** The key is read only by the SDK, from ``OPENAI_API_KEY``. It
is never stored on a record, never serialized, never logged, and never included
in an error message -- :func:`redact` scrubs any value that happens to appear in
exception text before it is recorded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

API_KEY_ENV = "OPENAI_API_KEY"
API_KEY_NOT_CONFIGURED = "OPENAI_API_KEY_NOT_CONFIGURED"


class ApiKeyNotConfigured(RuntimeError):
    """Raised instead of making a call when the key environment variable is absent."""


def api_key_configured() -> bool:
    """Whether the key variable exists and is non-empty. The value is never read out."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def redact(text: str) -> str:
    """Remove the API key from arbitrary text before it is stored or printed."""
    key = os.environ.get(API_KEY_ENV, "")
    if key and key in text:
        text = text.replace(key, "<redacted>")
    return text


@dataclass(frozen=True)
class GenerationCall:
    """One model call, with everything reproducibility needs and nothing secret."""

    model_requested: str
    model_returned: str | None
    response_text: str | None
    finish_reason: str | None
    response_id: str | None
    created: int | None
    system_fingerprint: str | None
    temperature_requested: float | None
    temperature_sent: float | None
    usage: dict[str, Any] = field(default_factory=dict)
    requested_at: str = ""
    completed_at: str = ""
    http_requests_made: int = 0
    api_rejected_parameters: tuple[str, ...] = ()
    api_errors: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.response_text is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_requested": self.model_requested,
            "model_returned": self.model_returned,
            "response_text": self.response_text,
            "finish_reason": self.finish_reason,
            "response_id": self.response_id,
            "created": self.created,
            "system_fingerprint": self.system_fingerprint,
            "temperature_requested": self.temperature_requested,
            "temperature_sent": self.temperature_sent,
            "usage": dict(self.usage),
            "requested_at": self.requested_at,
            "completed_at": self.completed_at,
            "http_requests_made": self.http_requests_made,
            "api_rejected_parameters": list(self.api_rejected_parameters),
            "api_errors": list(self.api_errors),
            "error": self.error,
        }


def _usage_dict(usage: Any) -> dict[str, Any]:
    """Flatten SDK usage into plain JSON, keeping nested detail (e.g. reasoning tokens)."""
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump(exclude_none=True)
    if isinstance(usage, dict):
        return dict(usage)
    return {
        name: getattr(usage, name)
        for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, name, None) is not None
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_client():
    """Construct the SDK client. Raises before any network use if no key is set."""
    if not api_key_configured():
        raise ApiKeyNotConfigured(API_KEY_NOT_CONFIGURED)
    from openai import OpenAI

    return OpenAI()  # reads OPENAI_API_KEY itself; we never touch the value


def generate(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float | None,
    client: Any = None,
    allow_temperature_fallback: bool = True,
) -> GenerationCall:
    """Send one chat completion and return a structured record.

    Chat Completions is used because the public YATE implementation builds a
    chat-style message list, and the repair loop will need to append turns to it.

    Only the parameters asked for are sent -- no seed, top_p, reasoning_effort or
    token limits are invented. If the API *rejects* the temperature, the rejection
    is recorded verbatim and, when `allow_temperature_fallback` is set, exactly one
    further request is made without it; `temperature_sent` and
    `api_rejected_parameters` then say so. Every HTTP attempt is counted in
    `http_requests_made`.
    """
    client = client or build_client()
    requested_at = _now()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    attempts = 0
    rejected: list[str] = []
    errors: list[str] = []
    temperature_sent = temperature

    while True:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if temperature_sent is not None:
            kwargs["temperature"] = temperature_sent
        attempts += 1
        try:
            completion = client.chat.completions.create(**kwargs)
            break
        except Exception as exc:  # noqa: BLE001 -- recorded, never swallowed
            detail = redact(f"{type(exc).__name__}: {exc}")
            errors.append(detail)
            retryable = (
                allow_temperature_fallback
                and temperature_sent is not None
                and "temperature" in detail.lower()
            )
            if not retryable:
                return GenerationCall(
                    model_requested=model,
                    model_returned=None,
                    response_text=None,
                    finish_reason=None,
                    response_id=None,
                    created=None,
                    system_fingerprint=None,
                    temperature_requested=temperature,
                    temperature_sent=temperature_sent,
                    requested_at=requested_at,
                    completed_at=_now(),
                    http_requests_made=attempts,
                    api_rejected_parameters=tuple(rejected),
                    api_errors=tuple(errors),
                    error=detail,
                )
            # The API refuses this temperature for this model: record and retry
            # once without it rather than silently choosing a different value.
            rejected.append("temperature")
            temperature_sent = None

    choice = completion.choices[0] if completion.choices else None
    return GenerationCall(
        model_requested=model,
        model_returned=getattr(completion, "model", None),
        response_text=getattr(choice.message, "content", None) if choice else None,
        finish_reason=getattr(choice, "finish_reason", None) if choice else None,
        response_id=getattr(completion, "id", None),
        created=getattr(completion, "created", None),
        system_fingerprint=getattr(completion, "system_fingerprint", None),
        temperature_requested=temperature,
        temperature_sent=temperature_sent,
        usage=_usage_dict(getattr(completion, "usage", None)),
        requested_at=requested_at,
        completed_at=_now(),
        http_requests_made=attempts,
        api_rejected_parameters=tuple(rejected),
        api_errors=tuple(errors),
    )
