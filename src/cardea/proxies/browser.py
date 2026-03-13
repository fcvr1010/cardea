"""
Browser credential manager — auto-fill login forms via CDP.

Exposes a single endpoint that fills login forms in a remote Chromium
instance without the caller ever seeing the actual credentials.

Flow:
    1. Caller sends ``POST /browser/fill {"domain": "github.com"}``
    2. This module looks up the site config in ``[browser.sites.*]``
    3. Loads the credential from Podman/Docker secrets
    4. Connects to Chromium via CDP (Chrome DevTools Protocol)
    5. Fills each configured field via ``Runtime.evaluate``
    6. Returns ``{"status": "filled", "fields_filled": N}``

The Chromium instance must expose a CDP debugging port reachable from
this container (configured via ``[browser] cdp_endpoint``).

This module is **not** listed in ``[modules]`` — it is loaded
automatically by ``app.py`` when a ``[browser]`` config section exists.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import websockets.asyncio.client
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cardea.secrets import get_secret

logger = logging.getLogger(__name__)

PREFIX = "/browser"
TAG = "Browser"

router = APIRouter()


class FillRequest(BaseModel):
    domain: str


class FillResponse(BaseModel):
    status: str
    fields_filled: int


# ── Configuration ─────────────────────────────────────────────────────────────

_cdp_endpoint: str = ""
_sites: dict[str, dict[str, Any]] = {}


def configure(browser_config: dict[str, Any]) -> None:
    """Load browser config from the ``[browser]`` TOML section."""
    global _cdp_endpoint  # noqa: PLW0603
    _cdp_endpoint = browser_config.get("cdp_endpoint", "")
    if not _cdp_endpoint:
        logger.warning("[browser] cdp_endpoint not set — /browser/fill will fail")

    sites = browser_config.get("sites", {})
    for name, site_cfg in sites.items():
        _sites[name] = site_cfg
        logger.info(
            "Browser site: %s (pattern=%s, fields=%d)",
            name,
            site_cfg.get("url_pattern", "?"),
            len(site_cfg.get("fields", [])),
        )


def _find_site(domain: str) -> tuple[str, dict[str, Any]]:
    """Find a site config whose ``url_pattern`` matches *domain*.

    Returns ``(site_name, site_config)`` or raises 404.
    """
    for name, cfg in _sites.items():
        pattern = cfg.get("url_pattern", "")
        if pattern and pattern in domain:
            return name, cfg
    raise HTTPException(
        status_code=404,
        detail=f"No browser credential config found for domain '{domain}'.",
    )


# ── CDP helpers ───────────────────────────────────────────────────────────────


async def _get_ws_url() -> str:
    """Discover the WebSocket debugger URL for the first browser tab."""
    # The CDP endpoint is like "ws://vito:9222"; the HTTP JSON endpoint
    # uses the same host:port with the /json path.
    http_url = _cdp_endpoint.replace("ws://", "http://").replace("wss://", "https://")
    http_url = http_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{http_url}/json")
            resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach browser CDP at {http_url}/json: {exc}",
        ) from exc

    tabs = resp.json()
    for tab in tabs:
        if tab.get("type") == "page":
            ws_url = tab.get("webSocketDebuggerUrl", "")
            if ws_url:
                return str(ws_url)

    raise HTTPException(
        status_code=502,
        detail="No active browser tab found via CDP.",
    )


async def _cdp_evaluate(ws_url: str, expression: str) -> Any:  # noqa: ANN401
    """Execute a JavaScript expression in the browser via CDP."""
    async with websockets.asyncio.client.connect(ws_url) as ws:
        msg = json.dumps(
            {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "awaitPromise": False,
                    "returnByValue": True,
                },
            }
        )
        await ws.send(msg)
        result_raw = await ws.recv()
        return json.loads(result_raw)


def _build_fill_js(selector: str, value: str) -> str:
    """Build JS that sets a field value and dispatches input events.

    The value is JSON-encoded to safely handle quotes and special characters.
    """
    safe_value = json.dumps(value)
    safe_selector = json.dumps(selector)
    return f"""
    (() => {{
        const el = document.querySelector({safe_selector});
        if (!el) return {{ found: false }};
        el.focus();
        el.value = {safe_value};
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return {{ found: true }};
    }})()
    """


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.post("/fill", response_model=FillResponse)
async def fill_credentials(req: FillRequest) -> FillResponse:
    """Fill login form fields for *domain* using stored credentials."""
    site_name, site_cfg = _find_site(req.domain)

    # Load credentials from secret store.
    secret_name = site_cfg.get("secret", "")
    if not secret_name:
        raise HTTPException(
            status_code=500,
            detail=f"No 'secret' configured for browser site '{site_name}'.",
        )

    try:
        raw_secret = get_secret(secret_name)
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Secret '{secret_name}' not found for browser site '{site_name}'. "
                f"Create it with: podman secret create {secret_name} <file>"
            ),
        )

    # Parse credentials JSON: {"username": "...", "password": "..."}
    try:
        creds = json.loads(raw_secret)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Secret '{secret_name}' is not valid JSON. "
                f'Expected format: {{"username": "...", "password": "..."}}'
            ),
        )

    fields = site_cfg.get("fields", [])
    if not fields:
        raise HTTPException(
            status_code=500,
            detail=f"No 'fields' configured for browser site '{site_name}'.",
        )

    # Connect to browser via CDP.
    ws_url = await _get_ws_url()

    filled = 0
    for field in fields:
        selector = field.get("selector", "")
        key = field.get("key", "")
        value = creds.get(key, "")
        if not selector or not key:
            continue
        if not value:
            logger.warning(
                "Credential key '%s' empty/missing in secret '%s' for site '%s'",
                key,
                secret_name,
                site_name,
            )
            continue

        js = _build_fill_js(selector, value)
        result = await _cdp_evaluate(ws_url, js)

        # Check if the element was found.
        cdp_result = result.get("result", {}).get("result", {})
        value_data = cdp_result.get("value", {})
        if isinstance(value_data, dict) and not value_data.get("found", True):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Selector '{selector}' not found on the current page "
                    f"for site '{site_name}'."
                ),
            )

        filled += 1

    logger.info(
        "Browser fill: site=%s, domain=%s, fields_filled=%d",
        site_name,
        req.domain,
        filled,
    )

    return FillResponse(status="filled", fields_filled=filled)
