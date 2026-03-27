"""
Shared HTTP helpers for Cardea client modules.

    _resolve_base_url(base_url=None) -> str
    _request(method, url, *, params=None, json=None, timeout=30.0) -> httpx.Response

``_resolve_base_url`` determines the Cardea server URL in order:
1. Explicit ``base_url`` argument
2. ``CARDEA_URL`` environment variable
3. ``http://localhost:8000`` (default)

``_request`` wraps ``httpx.request`` with a default timeout and automatic
``.raise_for_status()``.  Callers extract the body with ``.json()``.
"""

from __future__ import annotations

import os

import httpx

_DEFAULT_BASE_URL = "http://localhost:8000"


def _resolve_base_url(base_url: str | None = None) -> str:
    """Return the Cardea server base URL.

    Resolution order:
    1. *base_url* argument (if not ``None``)
    2. ``CARDEA_URL`` environment variable
    3. ``http://localhost:8000``

    The URL is resolved at **call time**, not import time, so tests can
    set ``CARDEA_URL`` freely without import-order issues.
    """
    if base_url is not None:
        return base_url.rstrip("/")
    return os.environ.get("CARDEA_URL", _DEFAULT_BASE_URL).rstrip("/")


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, str | int] | None = None,
    json: dict[str, object] | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """Send an HTTP request and raise on non-2xx status.

    Returns the raw :class:`httpx.Response` so callers can call ``.json()``
    or inspect headers as needed.
    """
    response = httpx.request(
        method=method.upper(),
        url=url,
        params=params,
        json=json,
        timeout=timeout,
    )
    response.raise_for_status()
    return response
