"""Retry policies with exponential backoff, randomized jitter and Retry-After support."""

import asyncio
import logging
import random
from typing import Any, Callable, Coroutine, Optional, Tuple, Type

import httpx

logger = logging.getLogger(__name__)


class RetryPolicy:
    """Configurable exponential backoff retry strategy with jitter."""

    def __init__(
        self,
        max_retries: int = 5,
        base_delay_seconds: float = 2.0,
        max_delay_seconds: float = 60.0,
        jitter: bool = True,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.jitter = jitter

    def compute_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """
        Calculate backoff delay.
        Respects HTTP Retry-After header value if provided by server.
        """
        if retry_after is not None and retry_after > 0:
            # Respect server-instructed delay, capped at max_delay
            return min(retry_after, self.max_delay_seconds)

        delay = self.base_delay_seconds * (2 ** attempt)
        if self.jitter:
            delay += random.uniform(0.1, 1.0)
        return min(delay, self.max_delay_seconds)

    def is_retryable(self, exc: Exception) -> bool:
        """
        Determine if an exception represents a transient failure eligible for retry.
        Permanent client errors (400, 401, 403, 404) are NOT retried.
        """
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 429:  # Rate limited -> Retryable
                return True
            if 500 <= status < 600:  # Transient server error -> Retryable
                return True
            return False

        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)):
            return True

        return False

    def extract_retry_after(self, exc: Exception) -> Optional[float]:
        """Extract Retry-After header seconds from HTTP response if available."""
        if isinstance(exc, httpx.HTTPStatusError):
            retry_header = exc.response.headers.get("retry-after")
            if retry_header:
                try:
                    return float(retry_header)
                except ValueError:
                    pass
        return None


async def execute_with_retry(
    func: Callable[[], Coroutine[Any, Any, Any]],
    policy: RetryPolicy,
    operation_name: str = "operation",
) -> Any:
    """Execute an asynchronous coroutine with retry policy enforcement."""
    attempt = 0
    while True:
        try:
            return await func()
        except Exception as exc:
            if attempt >= policy.max_retries or not policy.is_retryable(exc):
                logger.error(
                    "Operation failed permanently or exceeded max retries",
                    extra={
                        "operation": operation_name,
                        "attempt": attempt,
                        "max_retries": policy.max_retries,
                        "error": str(exc),
                    }
                )
                raise exc

            retry_after = policy.extract_retry_after(exc)
            delay = policy.compute_delay(attempt, retry_after)
            logger.warning(
                "Transient error in operation, backing off",
                extra={
                    "operation": operation_name,
                    "attempt": attempt + 1,
                    "delay_seconds": round(delay, 2),
                    "error": str(exc),
                }
            )
            await asyncio.sleep(delay)
            attempt += 1
