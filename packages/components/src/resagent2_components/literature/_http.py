"""Small, process-local HTTP policy shared by the literature backends."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
import httpx
from resagent2_runtime.budget import DeadlineExceededError, current_budget, remaining_timeout


USER_AGENT = "ResAgent2/0.1 (+https://github.com/ylc123456789/ResAgent2)"
COOLDOWN_SECONDS = 60.0


class LiteratureSearchError(RuntimeError):
    """A search did not produce a valid, normalized result."""


class LiteratureUnavailableError(LiteratureSearchError):
    """A temporary availability failure; another source may be tried."""


def _retry_after(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(max(0, int(value)))
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            return max(0.0, (deadline - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0.0


class LiteratureHTTP:
    """Serialize requests to one source, including across backend instances.

    Each provider owns one module-level instance. This is not a cross-process
    limiter: deployments must not run parallel clients against the same quota.
    Cooldown fails promptly instead of sleeping for minutes inside an Agent.
    """

    def __init__(self, source: str, *, interval_seconds: float) -> None:
        self.source = source
        self.interval_seconds = interval_seconds
        self._lock = Lock()
        self._next_request_at = 0.0
        self._cooldown_until = 0.0

    def fetch(self, request: Callable[[], bytes], *, max_attempts: int) -> bytes:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        budget = current_budget()
        if not self._lock.acquire(timeout=budget.remaining_timeout() if budget else -1):
            raise DeadlineExceededError("execution deadline exceeded waiting for literature access")
        try:
            remaining = self._cooldown_until - time.monotonic()
            if remaining > 0:
                raise LiteratureUnavailableError(
                    f"{self.source} cooling down; retry in {remaining:.0f}s"
                )
            reason = ""
            for attempt in range(max_attempts):
                wait = self._next_request_at - time.monotonic()
                if wait > 0:
                    time.sleep(remaining_timeout(wait))
                if current_budget() is not None:
                    current_budget().remaining_timeout()
                retry_after = 0.0
                try:
                    return request()
                except httpx.HTTPStatusError as error:
                    status = error.response.status_code
                    retry_after = _retry_after(
                        error.response.headers.get("Retry-After")
                    )
                    # Do not include response bodies, query URLs or auth in errors.
                    reason = f"HTTP {status}"
                    if status == 429:
                        self._cooldown_until = time.monotonic() + max(
                            COOLDOWN_SECONDS, retry_after
                        )
                        raise LiteratureUnavailableError(
                            f"{self.source} HTTP 429; rate limited, cooldown active"
                        ) from None
                    if status != 408 and not 500 <= status <= 599:
                        raise LiteratureSearchError(
                            f"{self.source} HTTP {status}; request rejected"
                        ) from None
                    if retry_after > 0:
                        self._cooldown_until = time.monotonic() + max(
                            COOLDOWN_SECONDS, retry_after
                        )
                        raise LiteratureUnavailableError(
                            f"{self.source} {reason}; Retry-After cooldown active"
                        ) from None
                except DeadlineExceededError:
                    raise
                except (httpx.TransportError, TimeoutError, ConnectionError) as error:
                    reason = type(error).__name__
                finally:
                    self._next_request_at = time.monotonic() + self.interval_seconds
                if attempt < max_attempts - 1:
                    self._next_request_at = time.monotonic() + max(
                        self.interval_seconds, 3.0 * 2**attempt
                    )
            self._cooldown_until = time.monotonic() + COOLDOWN_SECONDS
            raise LiteratureUnavailableError(
                f"{self.source} request failed after {max_attempts} attempts: {reason}"
            )
        finally:
            self._lock.release()
