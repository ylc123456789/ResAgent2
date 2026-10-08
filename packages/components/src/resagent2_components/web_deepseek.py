"""DeepSeek hosted search behind the provider-neutral web search interface."""

from __future__ import annotations

from urllib.parse import urlparse

from resagent2_runtime.model_request import (
    ModelRequestClient, ModelRequestError, ModelRequestHTTPError,
)

from .web import (
    MAX_WEB_SNIPPET_CHARS, WebSearchError, WebSearchItem, WebSearchResult, _retry_after,
)


class DeepSeekWebSearchBackend:
    """One independent Messages request; no Agent or conversation history."""

    name = "deepseek"

    def __init__(
        self, client: ModelRequestClient, *, model: str = "deepseek-flash",
        max_tokens: int = 4096, max_uses: int = 5,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("search model must not be empty")
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("search max_tokens must be positive")
        if type(max_uses) is not int or max_uses < 1:
            raise ValueError("search max_uses must be positive")
        self.client = client
        self.model = model.strip()
        self.max_tokens = max_tokens
        self.max_uses = max_uses

    def set_trace_context(self, **kwargs) -> None:
        self.client.set_trace_context(**kwargs)

    def search(self, query: str, *, max_results: int) -> WebSearchResult:
        if not isinstance(query, str) or not query.strip():
            raise WebSearchError("web query must not be empty", error_type="invalid_query")
        if type(max_results) is not int or not 1 <= max_results <= 10:
            raise WebSearchError("max_results must be between 1 and 10", error_type="invalid_query")
        query = query.strip()
        try:
            payload = self.client.request({
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{
                    "role": "user",
                    "content": [{"type": "text", "text": f"Perform a web search for the query: {query}"}],
                }],
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": self.max_uses}],
            })
        except ModelRequestHTTPError as error:
            raise WebSearchError(
                f"DeepSeek search HTTP {error.status_code}; request rejected",
                error_type="rate_limited" if error.status_code == 429 else "request_rejected",
                retry_after=_retry_after(error.retry_after) or None,
            ) from None
        except ModelRequestError as error:
            raise WebSearchError(str(error), error_type=error.error_type) from None
        results, incomplete_reason = _search_items(payload)
        return WebSearchResult(
            provider=self.name, query=query,
            results=results, incomplete_reason=incomplete_reason,
        )


def _search_items(payload: dict) -> tuple[list[WebSearchItem], str | None]:
    """Use native sources and URL-matched citations, never generated prose."""
    blocks = payload.get("content")
    if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
        raise WebSearchError("DeepSeek returned invalid content blocks", error_type="invalid_response")
    if payload.get("stop_reason") != "end_turn":
        raise WebSearchError("DeepSeek search response did not finish with end_turn", error_type="incomplete_response")
    result_blocks = [block for block in blocks if block.get("type") == "web_search_tool_result"]
    if not result_blocks:
        raise WebSearchError("DeepSeek returned no native web search result block", error_type="search_not_executed")

    snippets: dict[str, str] = {}
    for block in blocks:
        if block.get("type") != "text":
            continue
        citations = block.get("citations", [])
        if not isinstance(citations, list):
            raise WebSearchError("DeepSeek returned invalid citations", error_type="invalid_response")
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            url, excerpt = citation.get("url"), citation.get("cited_text")
            if isinstance(url, str) and isinstance(excerpt, str) and excerpt and url not in snippets:
                snippets[url] = excerpt[:MAX_WEB_SNIPPET_CHARS]

    seen: set[str] = set()
    results = []
    incomplete_reason = None
    for block in result_blocks:
        content = block.get("content")
        if isinstance(content, dict) and content.get("type") == "web_search_tool_result_error":
            content = [content]
        if not isinstance(content, list):
            raise WebSearchError("DeepSeek returned invalid search results", error_type="invalid_response")
        for item in content:
            if not isinstance(item, dict):
                raise WebSearchError("DeepSeek returned an invalid search item", error_type="invalid_response")
            if item.get("type") == "web_search_tool_result_error":
                code = item.get("error_code")
                if code == "max_uses_exceeded":
                    incomplete_reason = code
                    continue
                raise WebSearchError(
                    "DeepSeek native web search failed",
                    error_type="rate_limited" if code == "too_many_requests" else "provider_error",
                )
            if item.get("type") != "web_search_result":
                raise WebSearchError("DeepSeek returned an invalid search item type", error_type="invalid_response")
            url = item.get("url")
            if not isinstance(url, str):
                raise WebSearchError("DeepSeek search result has no URL", error_type="invalid_response")
            try:
                parsed = urlparse(url)
                valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None and parsed.password is None
            except ValueError:
                valid = False
            if not valid:
                raise WebSearchError("DeepSeek search result has an invalid URL", error_type="invalid_response")
            if url in seen:
                continue
            seen.add(url)
            title = item.get("title")
            age = item.get("page_age")
            results.append(WebSearchItem(
                title=title.strip() if isinstance(title, str) and title.strip() else url,
                url=url,
                snippet=snippets.get(url, ""),
                published_at=age if isinstance(age, str) else None,
            ))
    if incomplete_reason and not results:
        raise WebSearchError(
            "DeepSeek native search reached its use limit without returning sources",
            error_type="search_limit_exceeded",
        )
    return results, incomplete_reason
