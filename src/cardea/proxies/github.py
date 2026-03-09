"""
GitHub reverse proxy.

Git operations (clone, fetch, push, …):
    Incoming:  /github/<owner>/<repo>.git/<rest>
    Outgoing:  https://github.com/<owner>/<repo>.git/<rest>
    Auth:      HTTP Basic (x-access-token:<PAT>)

REST API (issues, PRs, actions, …):
    Incoming:  /github/api/<path>
    Outgoing:  https://api.github.com/<path>
    Auth:      Bearer <PAT>

The PAT is read from the cardea_github_token secret or environment variable.

Configure git to route through the proxy with a one-time command:

    git config --global url."http://localhost:8000/github/".insteadOf "https://github.com/"
"""

import base64
import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from cardea.secrets import get_secret

logger = logging.getLogger(__name__)

PREFIX = "/github"
TAG = "GitHub"

GITHUB_BASE = "https://github.com"
GITHUB_API_BASE = "https://api.github.com"

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
        "authorization",
    ]
)

router = APIRouter()


def _resolve_token() -> str:
    """Return the GitHub PAT, or raise if not configured."""
    try:
        return get_secret("cardea_github_token")
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail=(
                "No GitHub token configured. "
                "Provide cardea_github_token as a secret or environment variable."
            ),
        )


def _strip_headers(request: Request) -> dict[str, str]:
    """Strip hop-by-hop and auth headers from the incoming request."""
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


# ── REST API proxy (must be registered before the catch-all) ─────────────────


@router.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def github_api_proxy(path: str, request: Request) -> StreamingResponse:
    token = _resolve_token()

    upstream_url = f"{GITHUB_API_BASE}/{path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    headers = _strip_headers(request)
    headers["Authorization"] = f"Bearer {token}"

    logger.debug("GitHub API proxy: %s /api/%s", request.method, path)

    return await _proxy(request, upstream_url, headers)


# ── Git HTTPS proxy (catch-all) ──────────────────────────────────────────────


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def github_git_proxy(path: str, request: Request) -> StreamingResponse:
    token = _resolve_token()

    upstream_url = f"{GITHUB_BASE}/{path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    headers = _strip_headers(request)
    creds = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    headers["Authorization"] = f"Basic {creds}"

    logger.debug("GitHub git proxy: %s /%s", request.method, path)

    return await _proxy(request, upstream_url, headers)
