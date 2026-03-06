"""
Telegram Bot API reverse proxy.

Incoming path:  /botX/<method>
Outgoing URL:   https://api.telegram.org/bot<TOKEN>/<method>

File download path:  /file/botX/<file_path>
File download URL:   https://api.telegram.org/file/bot<TOKEN>/<file_path>

The real token is read from the environment variable
TELEGRAM_TOKEN_FOR_BOT_<ALIAS> (uppercased).
"""

import logging
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

PREFIX = "/telegram"
TAG = "Telegram"

TELEGRAM_BASE = "https://api.telegram.org"

_HOP_BY_HOP = frozenset(
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

router = APIRouter()


def _resolve_token(bot_alias: str) -> str:
    """Return the real Telegram token for *bot_alias*, or raise if not configured."""
    env_var = f"TELEGRAM_TOKEN_FOR_BOT_{bot_alias.upper()}"
    token = os.environ.get(env_var)
    if not token:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No token configured for bot alias '{bot_alias}'. "
                f"Set the {env_var} environment variable before starting the proxy."
            ),
        )
    return token


def _upstream_headers(request: Request) -> dict[str, str]:
    """Strip hop-by-hop headers and return a clean dict for the upstream call."""
    return {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}


async def _proxy(
    request: Request,
    upstream_url: str,
    headers: dict[str, str],
) -> StreamingResponse:
    """Forward *request* to *upstream_url* and stream the response back."""
    client = httpx.AsyncClient(follow_redirects=True, timeout=None)
    upstream_request = client.build_request(
        method=request.method,
        url=upstream_url,
        headers=headers,
        content=request.stream(),
    )
    upstream_response = await client.send(upstream_request, stream=True)

    response_headers = {
        k: v
        for k, v in upstream_response.headers.items()
        if k.lower() not in _HOP_BY_HOP
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


@router.api_route(
    "/bot{bot_alias}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def telegram_proxy(
    bot_alias: str, path: str, request: Request
) -> StreamingResponse:
    token = _resolve_token(bot_alias)

    upstream_url = f"{TELEGRAM_BASE}/bot{token}/{path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    logger.debug(
        "Telegram proxy: %s /bot%s/%s -> /bot***/%s",
        request.method,
        bot_alias,
        path,
        path,
    )

    return await _proxy(request, upstream_url, _upstream_headers(request))


@router.api_route(
    "/file/bot{bot_alias}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def telegram_file_proxy(
    bot_alias: str, path: str, request: Request
) -> StreamingResponse:
    token = _resolve_token(bot_alias)

    upstream_url = f"{TELEGRAM_BASE}/file/bot{token}/{path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    logger.debug(
        "Telegram file proxy: %s /file/bot%s/%s -> /file/bot***/%s",
        request.method,
        bot_alias,
        path,
        path,
    )

    return await _proxy(request, upstream_url, _upstream_headers(request))
