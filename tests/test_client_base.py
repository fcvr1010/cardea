"""
Tests for the client base module (cardea.client._base).

Covers URL resolution logic and the _request wrapper.
"""

from unittest.mock import patch

import httpx
import respx
from httpx import Response

from cardea.client._base import _request, _resolve_base_url


class TestResolveBaseUrl:
    """Tests for _resolve_base_url."""

    def test_explicit_base_url(self):
        """Explicit base_url takes precedence over everything."""
        result = _resolve_base_url("http://explicit:9000")
        assert result == "http://explicit:9000"

    def test_explicit_base_url_strips_trailing_slash(self):
        """Trailing slashes are stripped from explicit URLs."""
        result = _resolve_base_url("http://explicit:9000/")
        assert result == "http://explicit:9000"

    @patch.dict("os.environ", {"CARDEA_URL": "http://from-env:8080"})
    def test_env_var_fallback(self):
        """Falls back to CARDEA_URL env var when base_url is None."""
        result = _resolve_base_url()
        assert result == "http://from-env:8080"

    @patch.dict("os.environ", {"CARDEA_URL": "http://from-env:8080/"})
    def test_env_var_strips_trailing_slash(self):
        """Trailing slashes are stripped from env var URLs."""
        result = _resolve_base_url()
        assert result == "http://from-env:8080"

    @patch.dict("os.environ", {}, clear=True)
    def test_default_fallback(self):
        """Falls back to http://localhost:8000 when nothing is set."""
        result = _resolve_base_url()
        assert result == "http://localhost:8000"

    @patch.dict("os.environ", {"CARDEA_URL": "http://ignored:1234"})
    def test_explicit_overrides_env(self):
        """Explicit base_url wins even when CARDEA_URL is set."""
        result = _resolve_base_url("http://explicit:5000")
        assert result == "http://explicit:5000"


class TestRequest:
    """Tests for _request."""

    @respx.mock
    def test_get_request(self):
        """_request sends a GET and returns the response."""
        route = respx.get("http://localhost:8000/test").mock(
            return_value=Response(200, json={"ok": True})
        )
        response = _request("GET", "http://localhost:8000/test")
        assert response.json() == {"ok": True}
        assert route.called

    @respx.mock
    def test_post_request_with_json(self):
        """_request sends JSON body for POST."""
        route = respx.post("http://localhost:8000/test").mock(
            return_value=Response(201, json={"id": 1})
        )
        response = _request("POST", "http://localhost:8000/test", json={"key": "value"})
        assert response.status_code == 201
        assert route.called

    @respx.mock
    def test_raises_on_error_status(self):
        """_request raises HTTPStatusError on non-2xx status."""
        respx.get("http://localhost:8000/fail").mock(
            return_value=Response(500, json={"error": "boom"})
        )
        try:
            _request("GET", "http://localhost:8000/fail")
            raise AssertionError("Expected HTTPStatusError")
        except httpx.HTTPStatusError as exc:
            assert exc.response.status_code == 500

    @respx.mock
    def test_passes_params(self):
        """_request forwards query parameters."""
        route = respx.get("http://localhost:8000/search", params={"q": "test"}).mock(
            return_value=Response(200, json=[])
        )
        _request("GET", "http://localhost:8000/search", params={"q": "test"})
        assert route.called
