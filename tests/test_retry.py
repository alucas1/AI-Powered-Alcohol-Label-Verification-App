"""Retry/backoff around the provider call (label_ai._with_retry and helpers).

The schedule is exercised with an injected ``sleep`` so nothing actually waits;
real OpenAI error types are raised so the retry/no-retry split is tested against
the same classes the client throws in production.
"""

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from label_ai import (
    MAX_RETRIES,
    RETRY_MAX_DELAY,
    _backoff_delay,
    _retry_after_seconds,
    _with_retry,
)

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _rate_limit(retry_after: str | None = None) -> RateLimitError:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(429, headers=headers, request=_REQUEST)
    return RateLimitError("rate limited", response=response, body=None)


def _server_error() -> InternalServerError:
    return InternalServerError("boom", response=httpx.Response(500, request=_REQUEST), body=None)


class _Recorder:
    """A sleep() stand-in that records the delays it was asked to wait."""

    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


# --- the retry loop -----------------------------------------------------------


def test_returns_immediately_on_success():
    sleep = _Recorder()
    assert _with_retry(lambda: "ok", sleep=sleep) == "ok"
    assert sleep.delays == []  # no failure, so never slept


def test_retries_then_succeeds():
    sleep = _Recorder()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _rate_limit()
        return "ok"

    assert _with_retry(flaky, sleep=sleep) == "ok"
    assert calls["n"] == 3
    assert len(sleep.delays) == 2  # slept before each of the two retries


def test_exhausts_retries_and_raises_last_error():
    sleep = _Recorder()

    def always_throttled():
        raise _server_error()

    with pytest.raises(InternalServerError):
        _with_retry(always_throttled, max_retries=2, sleep=sleep)
    assert len(sleep.delays) == 2  # one sleep per retry, none after the last


def test_default_attempt_budget():
    sleep = _Recorder()
    calls = {"n": 0}

    def always_throttled():
        calls["n"] += 1
        raise _rate_limit()

    with pytest.raises(RateLimitError):
        _with_retry(always_throttled, sleep=sleep)
    assert calls["n"] == MAX_RETRIES + 1  # first attempt plus MAX_RETRIES retries


def test_does_not_retry_non_retryable_error():
    sleep = _Recorder()
    bad = BadRequestError(
        "nope", response=httpx.Response(400, request=_REQUEST), body=None
    )

    def call():
        raise bad

    with pytest.raises(BadRequestError):
        _with_retry(call, sleep=sleep)
    assert sleep.delays == []  # a 400 is fatal, so fail fast with no backoff


def test_connection_and_timeout_errors_are_retried():
    for exc in (APIConnectionError(request=_REQUEST), APITimeoutError(_REQUEST)):
        sleep = _Recorder()
        calls = {"n": 0}

        def flaky(_exc=exc):
            calls["n"] += 1
            if calls["n"] < 2:
                raise _exc
            return "ok"

        assert _with_retry(flaky, sleep=sleep) == "ok"
        assert len(sleep.delays) == 1


# --- Retry-After handling -----------------------------------------------------


def test_retry_after_seconds_header_is_honored():
    sleep = _Recorder()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _rate_limit(retry_after="3")
        return "ok"

    _with_retry(flaky, sleep=sleep)
    assert sleep.delays == [3.0]  # waited exactly what the server asked


def test_retry_after_parses_seconds():
    assert _retry_after_seconds(_rate_limit(retry_after="2")) == 2.0


def test_retry_after_parses_http_date():
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    delay = _retry_after_seconds(_rate_limit(retry_after=format_datetime(when)))
    assert 25 <= delay <= 30  # roughly 30s out, allowing for clock/parse slack


def test_retry_after_absent_returns_none():
    assert _retry_after_seconds(_rate_limit()) is None  # no header
    assert _retry_after_seconds(APITimeoutError(_REQUEST)) is None  # no response at all


def test_retry_after_garbage_returns_none():
    assert _retry_after_seconds(_rate_limit(retry_after="soon")) is None


# --- backoff schedule ---------------------------------------------------------


def test_backoff_delay_stays_within_cap_and_is_non_negative():
    for attempt in range(8):
        for _ in range(50):
            delay = _backoff_delay(attempt)
            assert 0.0 <= delay <= RETRY_MAX_DELAY
