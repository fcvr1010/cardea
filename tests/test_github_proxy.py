"""
Tests for the GitHub proxy router.

We mock the upstream httpx call so no real network traffic is made.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from cardea.app import app

client = TestClient(app)

FAKE_TOKEN = "ghp_fakePersonalAccessToken1234567890"
ENV = {"cardea_github_token": FAKE_TOKEN}


def _fake_upstream_response(status: int = 200, body: bytes = b"") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "application/x-git-upload-pack-advertisement"}
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


# ── Token resolution ──────────────────────────────────────────────────────────


def test_missing_token_returns_503():
    """Requesting without cardea_github_token env var yields 503."""
    response = client.get("/github/owner/repo.git/info/refs")
    assert response.status_code == 503
    assert "cardea_github_token" in response.json()["detail"]


def test_missing_token_returns_503_api():
    """API route also yields 503 without cardea_github_token."""
    response = client.get("/github/api/repos/owner/repo/pulls")
    assert response.status_code == 503
    assert "cardea_github_token" in response.json()["detail"]


# ── Git proxy forwarding ─────────────────────────────────────────────────────


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_git_forwarded_to_upstream(mock_client_cls):
    """GET /github/owner/repo.git/info/refs is forwarded to github.com."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        response = client.get(
            "/github/owner/repo.git/info/refs?service=git-upload-pack"
        )

    assert response.status_code == 200
    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert "github.com/owner/repo.git/info/refs" in call_kwargs.kwargs["url"]


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_git_pat_injected_as_basic_auth(mock_client_cls):
    """The PAT is injected as Basic Auth; any incoming auth header is stripped."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        client.get(
            "/github/owner/repo.git/info/refs",
            headers={"Authorization": "Basic bogus"},
        )

    call_kwargs = mock_client_cls.return_value.build_request.call_args
    injected_auth = call_kwargs.kwargs["headers"]["Authorization"]

    expected = (
        "Basic " + base64.b64encode(f"x-access-token:{FAKE_TOKEN}".encode()).decode()
    )
    assert injected_auth == expected


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_git_query_string_preserved(mock_client_cls):
    """Query parameters (e.g. ?service=git-upload-pack) are forwarded verbatim."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        client.get("/github/owner/repo.git/info/refs?service=git-upload-pack")

    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert "service=git-upload-pack" in call_kwargs.kwargs["url"]


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_git_post_forwarded(mock_client_cls):
    """POST /github/.../git-upload-pack (fetch/clone body) is forwarded correctly."""
    fake_resp = _fake_upstream_response(body=b"\x00\x00\x00\x00")
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        response = client.post(
            "/github/owner/repo.git/git-upload-pack",
            content=b"\x00\x00\x00\x00",
            headers={"Content-Type": "application/x-git-upload-pack-request"},
        )

    assert response.status_code == 200
    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert call_kwargs.kwargs["method"] == "POST"


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_git_upstream_error_propagated(mock_client_cls):
    """Non-200 responses from GitHub are forwarded as-is."""
    fake_resp = _fake_upstream_response(status=403, body=b"Forbidden")
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        response = client.get("/github/owner/repo.git/info/refs")
    assert response.status_code == 403


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_git_client_closed_after_stream(mock_client_cls):
    """The httpx client and response are closed after the body is consumed."""
    fake_resp = _fake_upstream_response()
    mock_client = _make_mock_client(fake_resp)
    mock_client_cls.return_value = mock_client

    with patch.dict("os.environ", ENV):
        client.get("/github/owner/repo.git/info/refs")

    fake_resp.aclose.assert_awaited_once()
    mock_client.aclose.assert_awaited_once()


# ── REST API proxy forwarding ────────────────────────────────────────────────


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_api_forwarded_to_api_github(mock_client_cls):
    """GET /github/api/repos/owner/repo is forwarded to api.github.com."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        response = client.get("/github/api/repos/owner/repo")

    assert response.status_code == 200
    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert "api.github.com/repos/owner/repo" in call_kwargs.kwargs["url"]
    assert "github.com/api/" not in call_kwargs.kwargs["url"]


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_api_uses_bearer_auth(mock_client_cls):
    """API route injects Bearer token, not Basic Auth."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        client.get("/github/api/repos/owner/repo/pulls")

    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert call_kwargs.kwargs["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_api_query_string_preserved(mock_client_cls):
    """Query parameters on API calls are forwarded."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        client.get("/github/api/repos/owner/repo/pulls?state=open&per_page=5")

    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert "state=open" in call_kwargs.kwargs["url"]
    assert "per_page=5" in call_kwargs.kwargs["url"]


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_api_post_forwarded(mock_client_cls):
    """POST to API (e.g. create issue) is forwarded correctly."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        response = client.post(
            "/github/api/repos/owner/repo/issues",
            json={"title": "Bug", "body": "Details"},
        )

    assert response.status_code == 200
    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert call_kwargs.kwargs["method"] == "POST"
    assert "api.github.com/repos/owner/repo/issues" in call_kwargs.kwargs["url"]


@patch("cardea.proxies.github.httpx.AsyncClient")
def test_api_upstream_error_propagated(mock_client_cls):
    """Non-200 API responses are forwarded as-is."""
    fake_resp = _fake_upstream_response(status=422, body=b"Unprocessable")
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict("os.environ", ENV):
        response = client.get("/github/api/repos/owner/repo")
    assert response.status_code == 422
