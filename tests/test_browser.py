"""
Tests for the browser credential manager (CDP-based form filling).

Covers:
- Fill with valid config (mock CDP WebSocket)
- Domain not configured → 404
- Secret missing → 503
- CDP not reachable → 502
- Selector not found on page → 422
- URL pattern matching
- Invalid secret format → 500
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cardea.proxies import browser as browser_module

FAKE_CREDS = json.dumps({"username": "testuser", "password": "testpass123"})


def _build_test_app(browser_config: dict[str, Any]) -> FastAPI:
    """Create a minimal FastAPI app with the browser module."""
    # Reset module state.
    browser_module._cdp_endpoint = ""
    browser_module._sites.clear()

    browser_module.configure(browser_config)

    app = FastAPI()
    app.include_router(
        browser_module.router,
        prefix=browser_module.PREFIX,
        tags=[browser_module.TAG],
    )
    return app


BROWSER_CONFIG = {
    "cdp_endpoint": "ws://localhost:9222",
    "sites": {
        "github": {
            "url_pattern": "github.com/login",
            "secret": "browser_github",
            "fields": [
                {"selector": "#login_field", "key": "username"},
                {"selector": "#password", "key": "password"},
            ],
        },
        "amazon": {
            "url_pattern": "amazon.",
            "secret": "browser_amazon",
            "fields": [
                {"selector": "#ap_email", "key": "username"},
                {"selector": "#ap_password", "key": "password"},
            ],
        },
    },
}


# ── Domain matching ───────────────────────────────────────────────────────────


class TestDomainMatching:
    def test_unknown_domain_returns_404(self):
        app = _build_test_app(BROWSER_CONFIG)
        client = TestClient(app)
        response = client.post("/browser/fill", json={"domain": "unknown-site.com"})
        assert response.status_code == 404
        assert "unknown-site.com" in response.json()["detail"]

    def test_exact_pattern_match(self):
        _build_test_app(BROWSER_CONFIG)
        # github.com/login matches "github.com/login"
        name, cfg = browser_module._find_site("github.com/login")
        assert name == "github"

    def test_partial_pattern_match(self):
        _build_test_app(BROWSER_CONFIG)
        # "amazon.de/signin" contains "amazon."
        name, cfg = browser_module._find_site("amazon.de/signin")
        assert name == "amazon"


# ── Missing secret ────────────────────────────────────────────────────────────


class TestMissingSecret:
    def test_missing_secret_returns_503(self):
        app = _build_test_app(BROWSER_CONFIG)
        client = TestClient(app)

        # Mock CDP ws_url discovery
        with patch.object(
            browser_module, "_get_ws_url", new_callable=AsyncMock
        ) as mock_ws:
            mock_ws.return_value = "ws://localhost:9222/devtools/page/123"

            response = client.post("/browser/fill", json={"domain": "github.com/login"})
            assert response.status_code == 503
            assert "browser_github" in response.json()["detail"]


# ── Invalid secret format ─────────────────────────────────────────────────────


class TestInvalidSecret:
    @patch.dict("os.environ", {"browser_github": "not-valid-json"})
    def test_non_json_secret_returns_500(self):
        app = _build_test_app(BROWSER_CONFIG)
        client = TestClient(app)

        with patch.object(
            browser_module, "_get_ws_url", new_callable=AsyncMock
        ) as mock_ws:
            mock_ws.return_value = "ws://localhost:9222/devtools/page/123"

            response = client.post("/browser/fill", json={"domain": "github.com/login"})
            assert response.status_code == 500
            assert "not valid JSON" in response.json()["detail"]


# ── CDP not reachable ─────────────────────────────────────────────────────────


class TestCdpUnreachable:
    @patch.dict("os.environ", {"browser_github": FAKE_CREDS})
    def test_cdp_unreachable_returns_502(self):
        app = _build_test_app(BROWSER_CONFIG)
        client = TestClient(app)

        # Don't mock _get_ws_url — let it try to connect and fail.
        # But we need to mock the httpx call inside _get_ws_url.
        with patch("cardea.proxies.browser.httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_cls.return_value = mock_client

            response = client.post("/browser/fill", json={"domain": "github.com/login"})
            assert response.status_code == 502
            assert "Cannot reach browser CDP" in response.json()["detail"]


# ── Selector not found ────────────────────────────────────────────────────────


class TestSelectorNotFound:
    @patch.dict("os.environ", {"browser_github": FAKE_CREDS})
    def test_selector_not_found_returns_422(self):
        app = _build_test_app(BROWSER_CONFIG)
        client = TestClient(app)

        # Mock CDP: ws_url discovery + evaluate returning {found: false}
        with (
            patch.object(
                browser_module, "_get_ws_url", new_callable=AsyncMock
            ) as mock_ws,
            patch.object(
                browser_module, "_cdp_evaluate", new_callable=AsyncMock
            ) as mock_eval,
        ):
            mock_ws.return_value = "ws://localhost:9222/devtools/page/123"
            mock_eval.return_value = {"result": {"result": {"value": {"found": False}}}}

            response = client.post("/browser/fill", json={"domain": "github.com/login"})
            assert response.status_code == 422
            assert "#login_field" in response.json()["detail"]


# ── Successful fill ───────────────────────────────────────────────────────────


class TestSuccessfulFill:
    @patch.dict("os.environ", {"browser_github": FAKE_CREDS})
    def test_fill_succeeds(self):
        app = _build_test_app(BROWSER_CONFIG)
        client = TestClient(app)

        with (
            patch.object(
                browser_module, "_get_ws_url", new_callable=AsyncMock
            ) as mock_ws,
            patch.object(
                browser_module, "_cdp_evaluate", new_callable=AsyncMock
            ) as mock_eval,
        ):
            mock_ws.return_value = "ws://localhost:9222/devtools/page/123"
            mock_eval.return_value = {"result": {"result": {"value": {"found": True}}}}

            response = client.post("/browser/fill", json={"domain": "github.com/login"})
            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "filled"
            assert data["fields_filled"] == 2

    @patch.dict("os.environ", {"browser_github": FAKE_CREDS})
    def test_fill_calls_cdp_evaluate_for_each_field(self):
        app = _build_test_app(BROWSER_CONFIG)
        client = TestClient(app)

        with (
            patch.object(
                browser_module, "_get_ws_url", new_callable=AsyncMock
            ) as mock_ws,
            patch.object(
                browser_module, "_cdp_evaluate", new_callable=AsyncMock
            ) as mock_eval,
        ):
            mock_ws.return_value = "ws://localhost:9222/devtools/page/123"
            mock_eval.return_value = {"result": {"result": {"value": {"found": True}}}}

            client.post("/browser/fill", json={"domain": "github.com/login"})

            # Should be called twice: once for username, once for password.
            assert mock_eval.call_count == 2

    @patch.dict("os.environ", {"browser_github": FAKE_CREDS})
    def test_fill_js_contains_correct_values(self):
        app = _build_test_app(BROWSER_CONFIG)
        client = TestClient(app)

        with (
            patch.object(
                browser_module, "_get_ws_url", new_callable=AsyncMock
            ) as mock_ws,
            patch.object(
                browser_module, "_cdp_evaluate", new_callable=AsyncMock
            ) as mock_eval,
        ):
            mock_ws.return_value = "ws://localhost:9222/devtools/page/123"
            mock_eval.return_value = {"result": {"result": {"value": {"found": True}}}}

            client.post("/browser/fill", json={"domain": "github.com/login"})

            # Check that the JS expressions contain the right selectors.
            calls = mock_eval.call_args_list
            first_js = calls[0][0][1]  # second positional arg = expression
            assert "#login_field" in first_js
            second_js = calls[1][0][1]
            assert "#password" in second_js
