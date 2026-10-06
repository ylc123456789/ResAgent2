"""Provider-neutral web search and bounded webpage fetching components.

These components own HTTP/provider normalization only. Model-facing tools live in
resagent2_capabilities.web.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from resagent2_runtime.http import NonPublicAddressError, ResponseTooLargeError, send_request


USER_AGENT = "ResAgent2/0.1 (+https://github.com/ylc123456789/ResAgent2)"


class WebSearchError(RuntimeError):
    """A web search did not produce a normalized result."""

    def __init__(self, message: str, *, error_type: str = "invalid_response",
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retry_after = retry_after


class WebFetchError(RuntimeError):
    """A webpage could not be fetched or converted to text."""

    def __init__(self, message: str, *, error_type: str = "invalid_response",
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class WebSearchItem:
    title: str
    url: str
    snippet: str
    score: float | None = None
    published_at: str | None = None


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    provider: str
    query: str
    results: list[WebSearchItem] = field(default_factory=list)


class WebSearchBackend(Protocol):
    """Provider-neutral search interface used by the Web capability."""

    name: str

    def search(self, query: str, *, max_results: int) -> WebSearchResult:
        """Return one bounded batch of search results."""


MAX_WEB_SNIPPET_CHARS = 2000


class TavilyWebSearchBackend:
    """Small Tavily adapter; provider responses never cross the component boundary."""

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(
        self, api_key: str, *, timeout_seconds: int = 30,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Tavily API key must not be empty")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def search(self, query: str, *, max_results: int) -> WebSearchResult:
        if not isinstance(query, str) or not query.strip():
            raise WebSearchError("web query must not be empty", error_type="invalid_query")
        if type(max_results) is not int or not 1 <= max_results <= 10:
            raise WebSearchError("max_results must be between 1 and 10", error_type="invalid_query")
        request = httpx.Request(
            "POST", self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            content=json.dumps({
                "query": query.strip(),
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            }).encode("utf-8"),
        )
        try:
            response = send_request(
                request, timeout=self.timeout_seconds,
                max_response_bytes=self.max_response_bytes,
            )
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            retry_after = _retry_after(error.response.headers.get("Retry-After"))
            error_type = "rate_limited" if status == 429 else "request_rejected"
            raise WebSearchError(
                f"Tavily HTTP {status}; request rejected",
                error_type=error_type, retry_after=retry_after or None,
            ) from None
        except (httpx.TimeoutException, TimeoutError):
            raise WebSearchError("Tavily request timed out", error_type="timeout") from None
        except ResponseTooLargeError:
            raise WebSearchError(
                "Tavily response exceeded the configured byte limit",
                error_type="response_too_large",
            ) from None
        except httpx.TransportError:
            raise WebSearchError("Tavily network request failed", error_type="network_error") from None
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("response is not an object")
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                raise ValueError("results is not a list")
            results = []
            for item in raw_results[:max_results]:
                if not isinstance(item, dict) or not item.get("url") or not item.get("title"):
                    continue
                results.append(WebSearchItem(
                    title=str(item["title"]).strip(),
                    url=str(item["url"]).strip(),
                    snippet=str(item.get("content", "")).strip()[:MAX_WEB_SNIPPET_CHARS],
                    score=float(item["score"]) if item.get("score") is not None else None,
                    published_at=str(item["published_date"]).strip()
                    if item.get("published_date") else None,
                ))
            return WebSearchResult(
                provider=self.name, query=query.strip(), results=results,
            )
        except (ValueError, TypeError, KeyError):
            raise WebSearchError("Tavily returned invalid JSON", error_type="invalid_response") from None


def _retry_after(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            return max(0.0, (deadline - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


class _HTMLTextParser(HTMLParser):
    _ignored = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._ignored:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in self._ignored and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not data.strip():
            return
        value = " ".join(data.split())
        if self._in_title:
            self.title_parts.append(value)
        else:
            self.text_parts.append(value)


@dataclass(frozen=True, slots=True)
class WebPage:
    source_url: str
    final_url: str
    title: str
    text: str
    content_type: str
    parser: str
    fetched_at: str


class WebPageFetcher:
    """Fetch one HTML/text page with bounded bytes and no browser execution."""

    def __init__(self, *, timeout_seconds: int = 30, max_response_bytes: int = 4 * 1024 * 1024) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def fetch(self, url: str) -> WebPage:
        try:
            response = self._request(url)
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            retry_after = _retry_after(error.response.headers.get("Retry-After"))
            raise WebFetchError(
                f"webpage HTTP {status}; request rejected",
                error_type="rate_limited" if status == 429 else "request_rejected",
                retry_after=retry_after or None,
            ) from None
        except (httpx.TimeoutException, TimeoutError):
            raise WebFetchError("webpage request timed out", error_type="timeout") from None
        except NonPublicAddressError:
            raise WebFetchError(
                "webpage host must resolve to a public address", error_type="invalid_url",
            ) from None
        except httpx.InvalidURL:
            raise WebFetchError("url must be an http(s) URL without credentials", error_type="invalid_url") from None
        except ResponseTooLargeError:
            raise WebFetchError(
                "webpage response exceeded the configured byte limit",
                error_type="response_too_large",
            ) from None
        except httpx.TransportError:
            raise WebFetchError("webpage network request failed", error_type="network_error") from None

        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
            raise WebFetchError(
                f"unsupported webpage content type: {content_type or 'unknown'}",
                error_type="unsupported_content_type",
            )
        try:
            body = response.content.decode("utf-8")
        except (UnicodeDecodeError, LookupError):
            raise WebFetchError("webpage is not valid UTF-8 text", error_type="invalid_encoding") from None
        if "\x00" in body:
            raise WebFetchError("webpage contains NUL bytes", error_type="invalid_encoding") from None
        final_url = str(response.url)
        if content_type == "text/plain":
            title, text, parser = "", body, "plain"
        else:
            parser_obj = _HTMLTextParser()
            try:
                parser_obj.feed(body)
                parser_obj.close()
            except Exception as error:
                raise WebFetchError("webpage HTML could not be parsed", error_type="parse_failed") from error
            title = " ".join(parser_obj.title_parts).strip()
            text = "\n".join(parser_obj.text_parts).strip()
            parser = "html.parser"
        if not text.strip():
            raise WebFetchError(
                "webpage has no extractable text", error_type="parse_failed",
            )
        return WebPage(
            source_url=url, final_url=final_url, title=title, text=text,
            content_type=content_type, parser=parser,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    def _request(self, url: str) -> httpx.Response:
        deadline = time.monotonic() + self.timeout_seconds
        current_url = url
        for redirect_count in range(6):
            _validate_url(current_url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebFetchError("webpage request timed out", error_type="timeout")
            request = httpx.Request(
                "GET", current_url,
                headers={"Accept": "text/html, text/plain", "User-Agent": USER_AGENT},
            )
            try:
                return send_request(
                    request, timeout=remaining,
                    max_response_bytes=self.max_response_bytes,
                    public_only=True,
                )
            except httpx.HTTPStatusError as error:
                location = error.response.headers.get("location")
                if error.response.status_code not in {301, 302, 303, 307, 308} or not location:
                    raise
                if redirect_count == 5:
                    raise WebFetchError("webpage exceeded 5 redirects", error_type="redirect_limit") from None
                current_url = urljoin(str(error.response.url), location)
        raise WebFetchError("webpage exceeded 5 redirects", error_type="redirect_limit")


def _validate_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        parsed.port
    except ValueError:
        raise WebFetchError("url must be an http(s) URL without credentials", error_type="invalid_url") from None
    if (parsed.scheme not in {"http", "https"} or parsed.username is not None
            or parsed.password is not None
            or not parsed.netloc or not parsed.hostname):
        raise WebFetchError("url must be an http(s) URL without credentials", error_type="invalid_url")
