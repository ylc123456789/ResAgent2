"""One-shot JSON model HTTP requests sharing the Run's wallet and deadline."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

import httpx

from .budget import (
    BudgetExhaustedError, DeadlineExceededError, RequestOutcome, current_budget,
    execution_budget,
)
from .http import ResponseTooLargeError, send_request
from ._trace import write_trace_record


class ModelRequestError(RuntimeError):
    """A safe transport failure; provider text and credentials are never exposed."""

    def __init__(self, message: str, *, error_type: str) -> None:
        super().__init__(message)
        self.error_type = error_type


class ModelRequestHTTPError(ModelRequestError):
    """HTTP status and a valid Retry-After header, without a raw HTTP response."""

    def __init__(self, status_code: int, retry_after: str | None = None) -> None:
        super().__init__(f"model request returned HTTP {status_code}", error_type="http_error")
        self.status_code = status_code
        self.retry_after = retry_after


def _retry_after(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit() and len(value) <= 10:
        return value
    try:
        datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT")
        return value
    except (TypeError, ValueError, OverflowError):
        return None


def _redact(value, api_key: str):
    if isinstance(value, str):
        for secret in (api_key, json.dumps(api_key, ensure_ascii=False)[1:-1]):
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {_redact(key, api_key): _redact(item, api_key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, api_key) for item in value]
    return value


def _usage(value):
    """Keep numeric diagnostics without provider-supplied response prose."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: item for key, original in value.items()
                if isinstance(key, str) and key.isascii() and key.isidentifier()
                and len(key) <= 64 and (item := _usage(original)) is not None}
    return None


def _stop_reason(payload: dict) -> str | None:
    value = payload.get("stop_reason", payload.get("finish_reason"))
    if (isinstance(value, str) and value.isascii() and len(value) <= 64
            and value.replace("_", "").isalnum()):
        return value
    return None


class ModelRequestClient:
    """POST caller-owned JSON once; the caller owns provider-specific schemas.

    A standalone request gets one local allowance. A nested request narrows the
    existing Run scope and persists its charge through the same usage port.
    """

    manages_usage = True

    def __init__(
        self, *, endpoint: str, api_key_env: str, timeout_seconds: float = 60,
        max_response_bytes: int = 2_000_000, trace_dir: Path | None = None,
        trace_level: str = "off", extra_headers: dict[str, str] | None = None,
        api_key_headers: tuple[str, ...] = ("Authorization",),
    ) -> None:
        url = httpx.URL(endpoint)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
            raise ValueError("model endpoint must be an HTTP(S) URL without credentials")
        if (not isinstance(api_key_env, str) or not api_key_env.strip()
                or "=" in api_key_env or "\x00" in api_key_env):
            raise ValueError("api_key_env must be a nonempty environment variable name")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if (not isinstance(max_response_bytes, int)
                or isinstance(max_response_bytes, bool) or max_response_bytes < 1):
            raise ValueError("max_response_bytes must be a positive integer")
        if trace_level not in {"off", "metadata", "full"}:
            raise ValueError("trace_level must be off, metadata or full")
        if trace_level != "off" and trace_dir is None:
            raise ValueError("trace_dir is required when trace_level is enabled")
        if (isinstance(api_key_headers, str) or not api_key_headers
                or any(not isinstance(name, str) or not name.strip()
                       for name in api_key_headers)):
            raise ValueError("api_key_headers must contain nonempty header names")
        self.endpoint = str(url)
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.trace_dir = Path(trace_dir).expanduser().resolve() if trace_dir else None
        self.trace_level = trace_level
        self.extra_headers = dict(extra_headers or {})
        self.api_key_headers = tuple(api_key_headers)
        self.last_attempts = 0
        self._trace_context: dict = {}
        self._trace_seq = 0

    def set_trace_context(self, **kwargs) -> None:
        """Attach operation/Run correlation fields to subsequent trace rows."""
        self._trace_context = dict(kwargs)

    def _write_trace(self, record: dict, api_key: str) -> None:
        if self.trace_level == "off" or self.trace_dir is None:
            return
        self._trace_seq += 1
        record = _redact({"sequence": self._trace_seq, **record}, api_key)
        write_trace_record(self.trace_dir, record)

    def request(self, body: dict) -> dict:
        """Return one JSON object or raise a safe, categorized transport error."""
        self.last_attempts = 0
        if not isinstance(body, dict):
            raise TypeError("model request body must be an object")
        request_text = json.dumps(body, ensure_ascii=False, allow_nan=False)
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ModelRequestError("missing model API key", error_type="missing_credentials")
        headers = httpx.Headers({"Content-Type": "application/json", **self.extra_headers})
        for name in self.api_key_headers:
            headers[name] = f"Bearer {api_key}" if name.lower() == "authorization" else api_key
        request = httpx.Request(
            "POST", self.endpoint, content=request_text.encode("utf-8"), headers=headers,
        )
        call_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        started = time.monotonic()
        raw_response_text = None
        response_bytes = None
        payload = None
        error_type = None
        validation_error = None
        status_code = None
        retry_after = None
        outcome: RequestOutcome = "unknown"
        parent_budget = current_budget()
        with execution_budget(max_llm_calls=1, timeout_seconds=self.timeout_seconds) as budget:
            budget.charge(call_id, 0)
            self.last_attempts = 1
            try:
                response = send_request(
                    request, timeout=budget.remaining_timeout(self.timeout_seconds),
                    max_response_bytes=self.max_response_bytes,
                )
                status_code = response.status_code
                outcome = "failed"
                try:
                    response_bytes = response.read()
                    raw_response_text = response_bytes.decode("utf-8", errors="replace")
                    payload = json.loads(response_bytes)
                    if not isinstance(payload, dict):
                        raise TypeError("response must be an object")
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                    raise ModelRequestError(
                        "model response must be a valid JSON object",
                        error_type="invalid_response",
                    ) from None
                outcome = "succeeded"
                return payload
            except httpx.HTTPStatusError as error:
                outcome = "failed"
                status_code = error.response.status_code
                retry_after = _retry_after(error.response.headers.get("Retry-After"))
                safe_error = ModelRequestHTTPError(status_code, retry_after)
                error_type, validation_error = safe_error.error_type, str(safe_error)
                raise safe_error from None
            except ResponseTooLargeError:
                outcome = "failed"
                error_type = "response_too_large"
                validation_error = "model response exceeded the byte limit"
                raise ModelRequestError(validation_error, error_type=error_type) from None
            except (BudgetExhaustedError, DeadlineExceededError) as error:
                error_type = ("budget_exhausted" if isinstance(error, BudgetExhaustedError)
                              else "deadline_exceeded")
                validation_error = "model request execution limit exceeded"
                raise
            except (httpx.TimeoutException, TimeoutError):
                error_type, validation_error = "timeout", "model request timed out"
                if parent_budget is not None:
                    try:
                        parent_budget.remaining_timeout()
                    except DeadlineExceededError as error:
                        error_type = "deadline_exceeded"
                        validation_error = "model request execution limit exceeded"
                        raise error from None
                raise ModelRequestError(validation_error, error_type=error_type) from None
            except httpx.TransportError:
                error_type, validation_error = "network_error", "model request transport failed"
                raise ModelRequestError(validation_error, error_type=error_type) from None
            except ModelRequestError as error:
                error_type, validation_error = error.error_type, str(error)
                raise
            finally:
                budget.usage.complete(call_id, 0, outcome)
                record = {
                    **self._trace_context, "call_id": call_id, "created_at": created_at,
                    "model": body.get("model"), "protocol": "json-model-request/v1",
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "retry_number": 0, "outcome": outcome,
                    "response_valid": outcome == "succeeded", "validation_error": validation_error,
                    "error_type": error_type, "http_status": status_code, "retry_after": retry_after,
                    "usage": _usage(payload.get("usage")) if isinstance(payload, dict) else None,
                    "stop_reason": _stop_reason(payload) if isinstance(payload, dict) else None,
                }
                if self.trace_level == "full":
                    record.update(request_text=request_text, raw_response_text=raw_response_text)
                else:
                    record.update(
                        request_sha256=hashlib.sha256(request_text.encode("utf-8")).hexdigest(),
                        response_sha256=(hashlib.sha256(response_bytes).hexdigest()
                                         if response_bytes is not None else None),
                    )
                self._write_trace(record, api_key)
