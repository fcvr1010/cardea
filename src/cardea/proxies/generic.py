"""
Config-driven generic HTTP reverse proxy.

Reads ``[services.*]`` sections from the Cardea configuration and creates
a FastAPI router for each service.  Every service is a simple reverse proxy
that forwards requests to an upstream URL while injecting credentials.

Supported ``auth.type`` values:

* ``bearer``  — ``Authorization: Bearer <secret>``
* ``basic``   — ``Authorization: Basic base64(<username>:<secret>)``
* ``header``  — injects the secret as a custom header (``auth.header_name``)
* ``query``   — appends the secret as a query parameter (``auth.param_name``)
* ``none``    — no credential injection

This module is **not** listed in ``[modules]`` — it is loaded automatically
by ``app.py`` whenever ``[services.*]`` sections are present.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from cardea.secrets import get_secret

logger = logging.getLogger(__name__)

# Headers that must not be forwarded between client ↔ upstream.
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

_VALID_AUTH_TYPES = frozenset(["bearer", "basic", "header", "query", "none"])


# ── Validation ────────────────────────────────────────────────────────────────


class ConfigError(Exception):
    """Raised when a ``[services.*]`` section is invalid."""


def validate_service(name: str, cfg: dict[str, Any]) -> None:
    """Validate a single service configuration block.

    Raises :class:`ConfigError` on the first problem found.
    """
    for field in ("prefix", "upstream"):
        if field not in cfg:
            raise ConfigError(f"[services.{name}] missing required field '{field}'")

    auth = cfg.get("auth", {})
    auth_type = auth.get("type")
    if auth_type is None:
        raise ConfigError(f"[services.{name}] missing 'auth.type'")
    if auth_type not in _VALID_AUTH_TYPES:
        raise ConfigError(
            f"[services.{name}] invalid auth.type '{auth_type}' "
            f"(valid: {', '.join(sorted(_VALID_AUTH_TYPES))})"
        )
    if auth_type != "none" and "secret" not in auth:
        raise ConfigError(
            f"[services.{name}] auth.type='{auth_type}' requires 'auth.secret'"
        )
    if auth_type == "basic" and "username" not in auth:
        raise ConfigError(
            f"[services.{name}] auth.type='basic' requires 'auth.username'"
        )
    if auth_type == "header" and "header_name" not in auth:
        raise ConfigError(
            f"[services.{name}] auth.type='header' requires 'auth.header_name'"
        )
    if auth_type == "query" and "param_name" not in auth:
        raise ConfigError(
            f"[services.{name}] auth.type='query' requires 'auth.param_name'"
        )


# ── Proxy helpers ─────────────────────────────────────────────────────────────


def _strip_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}


def _inject_auth(headers: dict[str, str], auth: dict[str, Any], secret: str) -> str:
    """Inject credentials into *headers* (mutated in place).

    Returns any extra query string fragment to append (empty string if none).
    """
    auth_type = auth["type"]

    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif auth_type == "basic":
        username = auth["username"]
        creds = base64.b64encode(f"{username}:{secret}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    elif auth_type == "header":
        headers[auth["header_name"]] = secret
    elif auth_type == "query":
        return f"{auth['param_name']}={secret}"
    # auth_type == "none" → nothing to do

    return ""


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


# ── Router factory ────────────────────────────────────────────────────────────


def _make_handler(service_name: str, upstream: str, auth: dict[str, Any]) -> Any:  # noqa: ANN401
    """Return an async route handler for one generic service."""

    async def _handler(path: str, request: Request) -> StreamingResponse:
        # Resolve secret (lazy, on each request — allows hot-adding secrets).
        if auth["type"] != "none":
            try:
                secret = get_secret(auth["secret"])
            except RuntimeError:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Secret '{auth['secret']}' not found for service "
                        f"'{service_name}'. Provide it as a Docker/Podman secret "
                        f"or environment variable."
                    ),
                )
        else:
            secret = ""

        upstream_url = f"{upstream}/{path}"

        # Preserve original query string.
        query_parts: list[str] = []
        if request.url.query:
            query_parts.append(str(request.url.query))

        headers = _strip_headers(request)
        extra_qs = _inject_auth(headers, auth, secret)
        if extra_qs:
            query_parts.append(extra_qs)

        if query_parts:
            upstream_url = f"{upstream_url}?{'&'.join(query_parts)}"

        logger.debug(
            "Generic proxy [%s]: %s %s -> %s",
            service_name,
            request.method,
            request.url.path,
            upstream_url.split("?")[0],
        )

        return await _proxy(request, upstream_url, headers)

    # Give the handler a unique name for FastAPI's operationId.
    _handler.__name__ = f"generic_proxy_{service_name.replace('-', '_')}"
    _handler.__qualname__ = _handler.__name__

    return _handler


def build_routers(
    services_config: dict[str, dict[str, Any]],
) -> list[tuple[APIRouter, str, str]]:
    """Build routers for all ``[services.*]`` config sections.

    Returns a list of ``(router, prefix, tag)`` tuples sorted by prefix
    length descending (longer prefixes match first).
    """
    result: list[tuple[APIRouter, str, str]] = []

    seen_prefixes: dict[str, str] = {}  # prefix → service name

    for name, cfg in services_config.items():
        validate_service(name, cfg)

        prefix = cfg["prefix"].rstrip("/")
        upstream = cfg["upstream"].rstrip("/")
        auth = cfg.get("auth", {"type": "none"})

        # Check for duplicate prefixes.
        if prefix in seen_prefixes:
            raise ConfigError(
                f"[services.{name}] prefix '{prefix}' conflicts with "
                f"[services.{seen_prefixes[prefix]}]"
            )
        seen_prefixes[prefix] = name

        tag = name.replace("-", " ").title()
        router = APIRouter()
        handler = _make_handler(name, upstream, auth)

        router.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        )(handler)

        result.append((router, prefix, tag))
        logger.info(
            "Generic service: %s (prefix=%s, upstream=%s, auth=%s)",
            name,
            prefix,
            upstream,
            auth["type"],
        )

    # Sort by prefix length descending so more specific routes match first.
    result.sort(key=lambda t: len(t[1]), reverse=True)

    return result
