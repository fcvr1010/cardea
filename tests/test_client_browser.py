"""
Tests for the browser client module (cardea.client.browser).

Uses respx to mock httpx requests — no server or network needed.
"""

import json

import httpx
import respx
from httpx import Response

from cardea.client.browser import fill_credentials

BASE = "http://localhost:8000"


@respx.mock
def test_fill_credentials_success():
    """fill_credentials sends POST /browser/fill with the domain."""
    route = respx.post(f"{BASE}/browser/fill").mock(
        return_value=Response(200, json={"status": "filled", "fields_filled": 2})
    )
    result = fill_credentials("github.com/login")
    assert result == {"status": "filled", "fields_filled": 2}
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["domain"] == "github.com/login"


@respx.mock
def test_fill_credentials_custom_base_url():
    """fill_credentials with explicit base_url."""
    route = respx.post("http://custom:9000/browser/fill").mock(
        return_value=Response(200, json={"status": "filled", "fields_filled": 1})
    )
    result = fill_credentials("amazon.de", base_url="http://custom:9000")
    assert result["status"] == "filled"
    assert route.called


@respx.mock
def test_fill_credentials_domain_not_found():
    """fill_credentials raises on 404 when domain is not configured."""
    respx.post(f"{BASE}/browser/fill").mock(
        return_value=Response(
            404,
            json={"detail": "No browser credential config found for domain 'x.com'"},
        )
    )
    try:
        fill_credentials("x.com")
        raise AssertionError("Expected HTTPStatusError")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 404


@respx.mock
def test_fill_credentials_server_error():
    """fill_credentials raises on 502 when CDP is unreachable."""
    respx.post(f"{BASE}/browser/fill").mock(
        return_value=Response(502, json={"detail": "Cannot reach browser CDP"})
    )
    try:
        fill_credentials("github.com/login")
        raise AssertionError("Expected HTTPStatusError")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 502
