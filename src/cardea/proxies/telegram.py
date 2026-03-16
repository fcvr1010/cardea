"""
Telegram Bot API reverse proxy.

Incoming path:  /botX/<method>
Outgoing URL:   https://api.telegram.org/bot<TOKEN>/<method>

File download path:  /file/botX/<file_path>
File download URL:   https://api.telegram.org/file/bot<TOKEN>/<file_path>

The real token is read from the secret (or environment variable)
cardea_telegram_token_for_bot_<alias> (lowercased).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from cardea.proxies._proxy_utils import HOP_BY_HOP_KEEP_AUTH, proxy, strip_headers
from cardea.secrets import get_secret

logger = logging.getLogger(__name__)

PREFIX = "/telegram"
TAG = "Telegram"

TELEGRAM_BASE = "https://api.telegram.org"

router = APIRouter()


def _resolve_token(bot_alias: str) -> str:
    """Return the real Telegram token for *bot_alias*, or raise if not configured."""
    secret_name = f"cardea_telegram_token_for_bot_{bot_alias.lower()}"
    try:
        return get_secret(secret_name)
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No token configured for bot alias '{bot_alias}'. "
                f"Provide {secret_name} as a secret or environment variable."
            ),
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

    headers = strip_headers(request, HOP_BY_HOP_KEEP_AUTH)
    return await proxy(
        request, upstream_url, headers, response_hop_by_hop=HOP_BY_HOP_KEEP_AUTH
    )


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

    headers = strip_headers(request, HOP_BY_HOP_KEEP_AUTH)
    return await proxy(
        request, upstream_url, headers, response_hop_by_hop=HOP_BY_HOP_KEEP_AUTH
    )
