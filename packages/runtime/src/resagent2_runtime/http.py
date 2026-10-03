"""Cancellable HTTP transport shared by model and tool requests."""

import asyncio

import httpx

from .budget import remaining_timeout


class ResponseTooLargeError(ValueError):
    """A decoded HTTP response exceeded the caller's byte limit."""


def send_request(
    request: httpx.Request, *, timeout: float,
    max_response_bytes: int | None = None, follow_redirects: bool = False,
) -> httpx.Response:
    """Bound total elapsed time and optionally decoded bytes and redirects."""
    if max_response_bytes is not None and max_response_bytes < 1:
        raise ValueError("max_response_bytes must be positive")
    timeout = remaining_timeout(timeout)

    async def send() -> httpx.Response:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=follow_redirects, max_redirects=5,
            ) as client:
                if max_response_bytes is None:
                    response = await client.send(request)
                    response.raise_for_status()
                    return response
                pending = request
                history = []
                while True:
                    # httpx normally reads a redirect body before following it.
                    # Discard it here so redirects cannot bypass the byte limit.
                    response = await client.send(pending, stream=True, follow_redirects=False)
                    try:
                        if follow_redirects and response.next_request is not None:
                            if len(history) >= 5:
                                raise httpx.TooManyRedirects(
                                    "Exceeded 5 redirects", request=response.request,
                                )
                            history.append(response)
                            pending = response.next_request
                            continue
                        response.raise_for_status()
                        content = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            if len(content) + len(chunk) > max_response_bytes:
                                raise ResponseTooLargeError(
                                    f"HTTP response exceeds {max_response_bytes} decoded bytes"
                                )
                            content.extend(chunk)
                        # aiter_bytes already decoded Content-Encoding. Present
                        # the bounded body without asking httpx to decode twice.
                        headers = response.headers.copy()
                        headers.pop("content-encoding", None)
                        headers.pop("content-length", None)
                        return httpx.Response(
                            response.status_code, content=bytes(content), headers=headers,
                            request=response.request, extensions=response.extensions,
                            history=history,
                        )
                    finally:
                        await response.aclose()

    # asyncio.run waits for the default executor on shutdown. A cancelled DNS
    # lookup would therefore delay returning until getaddrinfo finishes. Closing
    # this per-request loop cancels the HTTP work without waiting for the resolver
    # thread; its eventual result cannot resume this request.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(send())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
