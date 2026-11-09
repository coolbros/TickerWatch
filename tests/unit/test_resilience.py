"""Unit tests for retry, exponential backoff, jitter and resilience mechanisms."""

import asyncio
from unittest.mock import AsyncMock
import httpx
import pytest

from backend.ingestion.retry import RetryPolicy, execute_with_retry


def test_backoff_delay_exponential_growth():
    policy = RetryPolicy(base_delay_seconds=2.0, max_delay_seconds=60.0, jitter=False)
    assert policy.compute_delay(0) == 2.0
    assert policy.compute_delay(1) == 4.0
    assert policy.compute_delay(2) == 8.0
    assert policy.compute_delay(3) == 16.0
    assert policy.compute_delay(4) == 32.0
    assert policy.compute_delay(5) == 60.0  # Capped at max_delay


def test_backoff_with_jitter():
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=10.0, jitter=True)
    d = policy.compute_delay(0)
    assert 1.1 <= d <= 2.0  # 1.0 + uniform(0.1, 1.0)


def test_retry_after_header_override():
    policy = RetryPolicy(base_delay_seconds=2.0, max_delay_seconds=30.0)
    assert policy.compute_delay(0, retry_after=15.0) == 15.0
    assert policy.compute_delay(0, retry_after=45.0) == 30.0  # Capped


def test_is_retryable_classification():
    policy = RetryPolicy()

    req = httpx.Request("GET", "https://example.com")

    # 429 Rate Limit -> Retryable
    assert policy.is_retryable(httpx.HTTPStatusError("Rate Limit", request=req, response=httpx.Response(429, request=req)))

    # 503 Service Unavailable -> Retryable
    assert policy.is_retryable(httpx.HTTPStatusError("Service Unavailable", request=req, response=httpx.Response(503, request=req)))

    # 401 Unauthorized -> Permanent / Non-retryable
    assert not policy.is_retryable(httpx.HTTPStatusError("Unauthorized", request=req, response=httpx.Response(401, request=req)))

    # 404 Not Found -> Permanent / Non-retryable
    assert not policy.is_retryable(httpx.HTTPStatusError("Not Found", request=req, response=httpx.Response(404, request=req)))

    # Network timeout -> Retryable
    assert policy.is_retryable(httpx.ReadTimeout("Timeout"))


@pytest.mark.asyncio
async def test_execute_with_retry_succeeds_after_transient_failure():
    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.01, jitter=False)
    attempts = 0

    async def flaky_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("Temporary glitch")
        return "SUCCESS"

    result = await execute_with_retry(flaky_call, policy=policy, operation_name="flaky")
    assert result == "SUCCESS"
    assert attempts == 3


@pytest.mark.asyncio
async def test_execute_with_retry_fails_immediately_on_permanent_error():
    policy = RetryPolicy(max_retries=3, base_delay_seconds=0.01)
    attempts = 0
    req = httpx.Request("GET", "https://example.com")

    async def permanent_fail():
        nonlocal attempts
        attempts += 1
        raise httpx.HTTPStatusError("Forbidden", request=req, response=httpx.Response(403, request=req))

    with pytest.raises(httpx.HTTPStatusError):
        await execute_with_retry(permanent_fail, policy=policy, operation_name="perm_fail")

    assert attempts == 1  # Should not retry permanent 403


@pytest.mark.asyncio
async def test_execute_with_retry_exceeds_max_retries():
    """Verify that when transient errors persist beyond max_retries, the final error is raised."""
    policy = RetryPolicy(max_retries=2, base_delay_seconds=0.01, jitter=False)
    attempts = 0

    async def always_failing():
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("Persistent connection failure")

    with pytest.raises(httpx.ConnectTimeout):
        await execute_with_retry(always_failing, policy=policy, operation_name="always_fail")

    # Initial attempt (0) + 2 retries = 3 total attempts
    assert attempts == 3

