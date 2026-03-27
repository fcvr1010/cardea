"""
Browser credential client for Cardea — auto-fill login forms.

    fill_credentials(domain, base_url=None) -> dict

Server endpoint: ``POST /browser/fill``

Sends a domain to Cardea, which matches it against configured browser
sites, loads credentials from the secret store, connects to a Chromium
instance via CDP, and fills the login form fields.

Returns: ``{status: "filled", fields_filled: N}``
"""

from __future__ import annotations

from typing import Any

from cardea.client._base import _request, _resolve_base_url


def fill_credentials(
    domain: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Fill login form fields for *domain* using Cardea's browser manager.

    Cardea matches the domain against its configured ``[browser.sites.*]``
    entries, loads the credential from the secret store, and fills the
    form fields in the connected Chromium instance via CDP.

    Args:
        domain: Domain or URL fragment to match against configured sites
                (e.g. ``"github.com/login"``, ``"amazon.de"``).
        base_url: Override the Cardea server URL.

    Returns:
        Dict with ``status`` (``"filled"``) and ``fields_filled`` (int).
    """
    base = _resolve_base_url(base_url)
    payload: dict[str, object] = {"domain": domain}
    response = _request("POST", f"{base}/browser/fill", json=payload)
    result: dict[str, Any] = response.json()
    return result
