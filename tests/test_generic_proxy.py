"""
Tests for the config-driven generic HTTP reverse proxy.

Covers:
- Auth injection for every type (bearer, basic, header, query, none)
- Streaming response forwarding
- Query string preservation
- Header filtering (hop-by-hop + Authorization stripped)
- Config validation (missing fields, invalid auth type, duplicate prefixes)
- Error handling (missing secret → 503)
- Prefix ordering (longest first)
- All HTTP methods
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cardea.proxies.generic import (
    ConfigError,
    build_routers,
    validate_service,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_upstream_response(
    status: int = 200, body: bytes = b'{"ok":true}', content_type: str = "application/json"
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": content_type}
    resp.aclose = AsyncMock()

    async def _aiter_raw():
        yield body

    resp.aiter_raw = _aiter_raw
    return resp


def _make_mock_client(fake_resp: MagicMock) -> MagicMock:
    mock_client = MagicMock()
    mock_client.build_request.return_value = MagicMock()
    mock_client.send = AsyncMock(return_value=fake_resp)
    mock_client.aclose = AsyncMock()
    return mock_client


def _build_test_app(services: dict[str, dict[str, Any]]) -> FastAPI:
    """Create a minimal FastAPI app with generic routers."""
    app = FastAPI()
    routers = build_routers(services)
    for router, prefix, tag in routers:
        app.include_router(router, prefix=prefix, tags=[tag])
    return app


FAKE_TOKEN = "test_secret_token_12345"
FAKE_BASIC_USER = "x-access-token"


# ── Config validation ─────────────────────────────────────────────────────────


class TestValidation:
    def test_valid_bearer(self):
        validate_service("test", {
            "prefix": "/test",
            "upstream": "https://api.example.com",
            "auth": {"type": "bearer", "secret": "my_token"},
        })

    def test_valid_basic(self):
        validate_service("test", {
            "prefix": "/test",
            "upstream": "https://example.com",
            "auth": {"type": "basic", "username": "user", "secret": "my_pass"},
        })

    def test_valid_header(self):
        validate_service("test", {
            "prefix": "/test",
            "upstream": "https://example.com",
            "auth": {"type": "header", "header_name": "X-API-Key", "secret": "key"},
        })

    def test_valid_query(self):
        validate_service("test", {
            "prefix": "/test",
            "upstream": "https://example.com",
            "auth": {"type": "query", "param_name": "api_key", "secret": "key"},
        })

    def test_valid_none(self):
        validate_service("test", {
            "prefix": "/test",
            "upstream": "https://example.com",
            "auth": {"type": "none"},
        })

    def test_missing_prefix(self):
        with pytest.raises(ConfigError, match="missing required field 'prefix'"):
            validate_service("test", {
                "upstream": "https://example.com",
                "auth": {"type": "none"},
            })

    def test_missing_upstream(self):
        with pytest.raises(ConfigError, match="missing required field 'upstream'"):
            validate_service("test", {
                "prefix": "/test",
                "auth": {"type": "none"},
            })

    def test_missing_auth_type(self):
        with pytest.raises(ConfigError, match="missing 'auth.type'"):
            validate_service("test", {
                "prefix": "/test",
                "upstream": "https://example.com",
                "auth": {},
            })

    def test_invalid_auth_type(self):
        with pytest.raises(ConfigError, match="invalid auth.type 'magic'"):
            validate_service("test", {
                "prefix": "/test",
                "upstream": "https://example.com",
                "auth": {"type": "magic"},
            })

    def test_bearer_missing_secret(self):
        with pytest.raises(ConfigError, match="requires 'auth.secret'"):
            validate_service("test", {
                "prefix": "/test",
                "upstream": "https://example.com",
                "auth": {"type": "bearer"},
            })

    def test_basic_missing_username(self):
        with pytest.raises(ConfigError, match="requires 'auth.username'"):
            validate_service("test", {
                "prefix": "/test",
                "upstream": "https://example.com",
                "auth": {"type": "basic", "secret": "s"},
            })

    def test_header_missing_header_name(self):
        with pytest.raises(ConfigError, match="requires 'auth.header_name'"):
            validate_service("test", {
                "prefix": "/test",
                "upstream": "https://example.com",
                "auth": {"type": "header", "secret": "s"},
            })

    def test_query_missing_param_name(self):
        with pytest.raises(ConfigError, match="requires 'auth.param_name'"):
            validate_service("test", {
                "prefix": "/test",
                "upstream": "https://example.com",
                "auth": {"type": "query", "secret": "s"},
            })

    def test_duplicate_prefix_rejected(self):
        with pytest.raises(ConfigError, match="prefix '/test' conflicts"):
            build_routers({
                "svc-a": {
                    "prefix": "/test",
                    "upstream": "https://a.example.com",
                    "auth": {"type": "none"},
                },
                "svc-b": {
                    "prefix": "/test",
                    "upstream": "https://b.example.com",
                    "auth": {"type": "none"},
                },
            })


# ── Prefix ordering ──────────────────────────────────────────────────────────


class TestPrefixOrdering:
    def test_longer_prefix_first(self):
        routers = build_routers({
            "short": {
                "prefix": "/api",
                "upstream": "https://short.example.com",
                "auth": {"type": "none"},
            },
            "long": {
                "prefix": "/api/v2",
                "upstream": "https://long.example.com",
                "auth": {"type": "none"},
            },
        })
        prefixes = [prefix for _, prefix, _ in routers]
        assert prefixes == ["/api/v2", "/api"]


# ── Bearer auth ───────────────────────────────────────────────────────────────


class TestBearerAuth:
    @patch.dict("os.environ", {"my_token": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_bearer_injects_header(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "test-api": {
                "prefix": "/test",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "my_token"},
            },
        })
        client = TestClient(app)
        response = client.get("/test/some/path")
        assert response.status_code == 200

        built_req = mock_client.build_request.call_args
        headers = built_req.kwargs.get("headers") or built_req[1].get("headers", {})
        assert headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"

    @patch.dict("os.environ", {"my_token": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_bearer_upstream_url(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "test-api": {
                "prefix": "/test",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "my_token"},
            },
        })
        client = TestClient(app)
        client.get("/test/repos/owner/repo")

        built_req = mock_client.build_request.call_args
        url = str(built_req.kwargs.get("url") or built_req[1].get("url", ""))
        assert url == "https://api.example.com/repos/owner/repo"


# ── Basic auth ────────────────────────────────────────────────────────────────


class TestBasicAuth:
    @patch.dict("os.environ", {"my_pass": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_basic_injects_header(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "git": {
                "prefix": "/git",
                "upstream": "https://github.com",
                "auth": {"type": "basic", "username": FAKE_BASIC_USER, "secret": "my_pass"},
            },
        })
        client = TestClient(app)
        client.get("/git/owner/repo.git/info/refs")

        expected_creds = base64.b64encode(
            f"{FAKE_BASIC_USER}:{FAKE_TOKEN}".encode()
        ).decode()

        built_req = mock_client.build_request.call_args
        headers = built_req.kwargs.get("headers") or built_req[1].get("headers", {})
        assert headers.get("Authorization") == f"Basic {expected_creds}"


# ── Header auth ───────────────────────────────────────────────────────────────


class TestHeaderAuth:
    @patch.dict("os.environ", {"my_key": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_header_injects_custom_header(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "custom": {
                "prefix": "/custom",
                "upstream": "https://api.example.com",
                "auth": {
                    "type": "header",
                    "header_name": "X-API-Key",
                    "secret": "my_key",
                },
            },
        })
        client = TestClient(app)
        client.get("/custom/endpoint")

        built_req = mock_client.build_request.call_args
        headers = built_req.kwargs.get("headers") or built_req[1].get("headers", {})
        assert headers.get("X-API-Key") == FAKE_TOKEN


# ── Query auth ────────────────────────────────────────────────────────────────


class TestQueryAuth:
    @patch.dict("os.environ", {"my_key": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_query_appends_param(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "maps": {
                "prefix": "/maps",
                "upstream": "https://maps.googleapis.com",
                "auth": {
                    "type": "query",
                    "param_name": "key",
                    "secret": "my_key",
                },
            },
        })
        client = TestClient(app)
        client.get("/maps/api/geocode/json")

        built_req = mock_client.build_request.call_args
        url = str(built_req.kwargs.get("url") or built_req[1].get("url", ""))
        assert f"key={FAKE_TOKEN}" in url


# ── No auth ───────────────────────────────────────────────────────────────────


class TestNoAuth:
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_none_auth_no_header(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "public": {
                "prefix": "/public",
                "upstream": "https://public.example.com",
                "auth": {"type": "none"},
            },
        })
        client = TestClient(app)
        client.get("/public/data")

        built_req = mock_client.build_request.call_args
        headers = built_req.kwargs.get("headers") or built_req[1].get("headers", {})
        assert "Authorization" not in headers


# ── Query string preservation ─────────────────────────────────────────────────


class TestQueryString:
    @patch.dict("os.environ", {"tok": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_query_string_forwarded(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "api": {
                "prefix": "/api",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "tok"},
            },
        })
        client = TestClient(app)
        client.get("/api/search?q=hello&page=2")

        built_req = mock_client.build_request.call_args
        url = str(built_req.kwargs.get("url") or built_req[1].get("url", ""))
        assert "q=hello" in url
        assert "page=2" in url

    @patch.dict("os.environ", {"tok": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_query_auth_combined_with_existing_qs(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "maps": {
                "prefix": "/maps",
                "upstream": "https://maps.example.com",
                "auth": {"type": "query", "param_name": "key", "secret": "tok"},
            },
        })
        client = TestClient(app)
        client.get("/maps/geocode?address=Zurich")

        built_req = mock_client.build_request.call_args
        url = str(built_req.kwargs.get("url") or built_req[1].get("url", ""))
        assert "address=Zurich" in url
        assert f"key={FAKE_TOKEN}" in url


# ── Header stripping ─────────────────────────────────────────────────────────


class TestHeaderStripping:
    @patch.dict("os.environ", {"tok": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_authorization_header_stripped(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "api": {
                "prefix": "/api",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "tok"},
            },
        })
        client = TestClient(app)
        client.get("/api/resource", headers={"Authorization": "Bearer should-be-stripped"})

        built_req = mock_client.build_request.call_args
        headers = built_req.kwargs.get("headers") or built_req[1].get("headers", {})
        # The incoming Authorization was stripped, replaced by the proxy's own.
        assert headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"

    @patch.dict("os.environ", {"tok": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_hop_by_hop_headers_stripped(self, mock_client_cls):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "api": {
                "prefix": "/api",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "tok"},
            },
        })
        client = TestClient(app)
        client.get("/api/resource", headers={"Connection": "keep-alive"})

        built_req = mock_client.build_request.call_args
        headers = built_req.kwargs.get("headers") or built_req[1].get("headers", {})
        assert "connection" not in {k.lower() for k in headers}


# ── Error handling ────────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_missing_secret_returns_503(self):
        app = _build_test_app({
            "api": {
                "prefix": "/api",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "nonexistent_secret"},
            },
        })
        client = TestClient(app)
        response = client.get("/api/test")
        assert response.status_code == 503
        assert "nonexistent_secret" in response.json()["detail"]


# ── HTTP methods ──────────────────────────────────────────────────────────────


class TestHttpMethods:
    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
    @patch.dict("os.environ", {"tok": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_all_methods_forwarded(self, mock_client_cls, method):
        fake_resp = _fake_upstream_response()
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "api": {
                "prefix": "/api",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "tok"},
            },
        })
        client = TestClient(app)
        response = getattr(client, method.lower())("/api/resource")
        assert response.status_code == 200

        built_req = mock_client.build_request.call_args
        assert built_req.kwargs.get("method") == method or built_req[1].get("method") == method


# ── Streaming ─────────────────────────────────────────────────────────────────


class TestStreaming:
    @patch.dict("os.environ", {"tok": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_response_body_streamed(self, mock_client_cls):
        body = b"chunk1chunk2chunk3"
        fake_resp = _fake_upstream_response(body=body)
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "api": {
                "prefix": "/api",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "tok"},
            },
        })
        client = TestClient(app)
        response = client.get("/api/data")
        assert response.content == body

    @patch.dict("os.environ", {"tok": FAKE_TOKEN})
    @patch("cardea.proxies.generic.httpx.AsyncClient")
    def test_upstream_status_code_preserved(self, mock_client_cls):
        fake_resp = _fake_upstream_response(status=404, body=b'{"message":"Not Found"}')
        mock_client = _make_mock_client(fake_resp)
        mock_client_cls.return_value = mock_client

        app = _build_test_app({
            "api": {
                "prefix": "/api",
                "upstream": "https://api.example.com",
                "auth": {"type": "bearer", "secret": "tok"},
            },
        })
        client = TestClient(app)
        response = client.get("/api/missing")
        assert response.status_code == 404
