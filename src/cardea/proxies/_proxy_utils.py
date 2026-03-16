"""
Shared streaming-proxy logic used by both the generic and Telegram proxies.

The ``streaming_proxy`` function forwards an incoming FastAPI ``Request`` to an
upstream URL and streams the response body back chunk-by-chunk.

``HOP_BY_HOP`` is the base set of hop-by-hop headers that must not be
forwarded.  Callers that need to suppress additional headers (e.g.
``"authorization"`` in the generic proxy) pass them via *extra_hop_by_hop*.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse

# Headers that must not be forwarded between client and upstream.
# Individual proxies can extend this set via the *extra_hop_by_hop* parameter.
HOP_BY_HOP: frozenset[str] = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    ]
)


def strip_headers(
    request: Request,
    *,
    extra_hop_by_hop: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Return request headers with hop-by-hop entries removed."""
    blocked = HOP_BY_HOP | extra_hop_by_hop
    return {k: v for k, v in request.headers.items() if k.lower() not in blocked}


async def streaming_proxy(
    request: Request,
    upstream_url: str,
    headers: dict[str, str],
    *,
    extra_hop_by_hop: frozenset[str] = frozenset(),
) -> StreamingResponse:
    """Forward *request* to *upstream_url* and stream the response back.

    *extra_hop_by_hop* lists additional header names (lowercase) to strip from
    the **response** beyond the base :data:`HOP_BY_HOP` set.
    """
    blocked = HOP_BY_HOP | extra_hop_by_hop

    client = httpx.AsyncClient(follow_redirects=True, timeout=None)
    upstream_request = client.build_request(
        method=request.method,
        url=upstream_url,
        headers=headers,
        content=request.stream(),
    )
    upstream_response = await client.send(upstream_request, stream=True)

    response_headers = {
        k: v for k, v in upstream_response.headers.items() if k.lower() not in blocked
    }

    async def _body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        content=_body(),
        status_code=upstream_response.status_code,
        headers=response_headers,
    )
