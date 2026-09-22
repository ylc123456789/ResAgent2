"""Cancellable HTTP transport shared by model and tool requests."""

import asyncio

import httpx

from .budget import remaining_timeout


def send_request(request: httpx.Request, *, timeout: float) -> httpx.Response:
    """Enforce total elapsed time, including connection and slow response bodies."""
    timeout = remaining_timeout(timeout)

    async def send() -> httpx.Response:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.send(request)
                response.raise_for_status()
                return response

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
