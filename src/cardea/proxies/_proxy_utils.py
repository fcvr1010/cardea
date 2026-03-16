"""Shared streaming-proxy helpers used by multiple proxy modules.

Centralises the hop-by-hop header sets and the streaming ``proxy()``
function so that ``generic.py`` and ``telegram.py`` don't duplicate them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse

# ── Hop-by-hop header sets ───────────────────────────────────────────────────

# Base set of HTTP hop-by-hop headers that must never be forwarded.
_HOP_BY_HOP_BASE: frozenset[str] = frozenset(
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

# Extended set that also strips "authorization" — used by the generic proxy
# because it injects its own Authorization header per-service.
HOP_BY_HOP: frozenset[str] = _HOP_BY_HOP_BASE | frozenset(["authorization"])

# Minimal set without "authorization" — used by proxies (e.g. Telegram) that
# never touch the Authorization header.
HOP_BY_HOP_KEEP_AUTH: frozenset[str] = _HOP_BY_HOP_BASE


# ── Shared helpers ───────────────────────────────────────────────────────────


def strip_headers(
    request: Request,
    hop_by_hop: frozenset[str] = HOP_BY_HOP,
) -> dict[str, str]:
    """Return request headers with hop-by-hop entries removed."""
    return {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}


async def proxy(
    request: Request,
    upstream_url: str,
    headers: dict[str, str],
    hop_by_hop: frozenset[str] = HOP_BY_HOP,
) -> StreamingResponse:
    """Forward *request* to *upstream_url* and stream the response back.

    If ``client.send()`` raises, the underlying ``httpx.AsyncClient`` is
    closed immediately to prevent resource leaks.
    """
    client = httpx.AsyncClient(follow_redirects=True, timeout=None)
    upstream_request = client.build_request(
        method=request.method,
        url=upstream_url,
        headers=headers,
        content=request.stream(),
    )
    try:
        upstream_response = await client.send(upstream_request, stream=True)
    except Exception:
        await client.aclose()
        raise

    response_headers = {
        k: v
        for k, v in upstream_response.headers.items()
        if k.lower() not in hop_by_hop
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
