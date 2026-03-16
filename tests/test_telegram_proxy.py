"""
Tests for the Telegram proxy router.

We mock the upstream httpx call so no real network traffic is made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from cardea.app import app

client = TestClient(app)

FAKE_TOKEN = "123456789:AABBccDDeeFFggHHiiJJkkLLmmNN"
BOT_ALIAS = "myproject"
ENV_VAR = f"cardea_telegram_token_for_bot_{BOT_ALIAS.lower()}"


def _fake_upstream_response(
    status: int = 200, body: bytes = b'{"ok":true}'
) -> MagicMock:
    """Build a mock that looks like an httpx streaming response."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "application/json"}
    resp.aclose = AsyncMock()

    async def _aiter_raw():
        yield body

    resp.aiter_raw = _aiter_raw
    return resp


def _make_mock_client(fake_resp: MagicMock) -> MagicMock:
    """Return a mock httpx.AsyncClient instance (not used as context manager)."""
    mock_client = MagicMock()
    mock_client.build_request.return_value = MagicMock()
    mock_client.send = AsyncMock(return_value=fake_resp)
    mock_client.aclose = AsyncMock()
    return mock_client


# ── Token resolution ─────────────────────────────────────────────────────────


def test_missing_token_returns_503():
    """Requesting a bot alias with no matching credential yields 503."""
    response = client.get("/telegram/botunknown/getMe")
    assert response.status_code == 503
    assert "cardea_telegram_token_for_bot_unknown" in response.json()["detail"]


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── Proxy forwarding ─────────────────────────────────────────────────────────


@pytest.fixture
def token_env():
    with patch.dict("os.environ", {ENV_VAR: FAKE_TOKEN}):
        yield


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_get_forwarded_to_upstream(mock_client_cls, token_env):
    """GET /telegram/botX/getMe should be forwarded to the real Telegram URL."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    response = client.get(f"/telegram/bot{BOT_ALIAS}/getMe")

    assert response.status_code == 200
    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert FAKE_TOKEN in call_kwargs.kwargs["url"]
    assert BOT_ALIAS not in call_kwargs.kwargs["url"]


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_query_string_preserved(mock_client_cls, token_env):
    """Query parameters must be forwarded verbatim to upstream."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    client.get(f"/telegram/bot{BOT_ALIAS}/sendMessage?chat_id=42&text=hello")

    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert "chat_id=42" in call_kwargs.kwargs["url"]
    assert "text=hello" in call_kwargs.kwargs["url"]


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_post_forwarded(mock_client_cls, token_env):
    """POST requests (e.g. sendMessage via JSON body) are forwarded correctly."""
    fake_resp = _fake_upstream_response(body=b'{"ok":true,"result":{"message_id":1}}')
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    response = client.post(
        f"/telegram/bot{BOT_ALIAS}/sendMessage",
        json={"chat_id": 42, "text": "hello"},
    )

    assert response.status_code == 200
    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert call_kwargs.kwargs["method"] == "POST"


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_upstream_error_propagated(mock_client_cls, token_env):
    """Non-200 status codes from Telegram are forwarded as-is."""
    fake_resp = _fake_upstream_response(
        status=400, body=b'{"ok":false,"description":"Bad Request"}'
    )
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    response = client.get(f"/telegram/bot{BOT_ALIAS}/getMe")
    assert response.status_code == 400


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_bot_alias_case_insensitive_cred(mock_client_cls):
    """Alias lookup is case-insensitive: env var key is always uppercased."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    with patch.dict(
        "os.environ", {"cardea_telegram_token_for_bot_mixedcase": FAKE_TOKEN}
    ):
        response = client.get("/telegram/botMixedCase/getMe")
    assert response.status_code == 200


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_client_closed_after_stream(mock_client_cls, token_env):
    """The httpx client and response must be closed after the body is consumed."""
    fake_resp = _fake_upstream_response()
    mock_client = _make_mock_client(fake_resp)
    mock_client_cls.return_value = mock_client

    client.get(f"/telegram/bot{BOT_ALIAS}/getMe")

    fake_resp.aclose.assert_awaited_once()
    mock_client.aclose.assert_awaited_once()


# ── File download proxy ─────────────────────────────────────────────────────


def test_file_missing_token_returns_503():
    """File download route returns 503 without a matching credential."""
    response = client.get("/telegram/file/botunknown/documents/file_123.pdf")
    assert response.status_code == 503
    assert "cardea_telegram_token_for_bot_unknown" in response.json()["detail"]


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_file_get_forwarded_to_upstream(mock_client_cls, token_env):
    """GET /telegram/file/botX/path is forwarded to the real Telegram file URL."""
    fake_resp = _fake_upstream_response(body=b"file-content")
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    response = client.get(f"/telegram/file/bot{BOT_ALIAS}/documents/file_123.pdf")

    assert response.status_code == 200
    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert f"/file/bot{FAKE_TOKEN}/" in call_kwargs.kwargs["url"]
    assert "documents/file_123.pdf" in call_kwargs.kwargs["url"]
    assert BOT_ALIAS not in call_kwargs.kwargs["url"]


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_file_query_string_preserved(mock_client_cls, token_env):
    """Query parameters on file download are forwarded."""
    fake_resp = _fake_upstream_response()
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    client.get(f"/telegram/file/bot{BOT_ALIAS}/photos/pic.jpg?size=large")

    call_kwargs = mock_client_cls.return_value.build_request.call_args
    assert "size=large" in call_kwargs.kwargs["url"]


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_file_upstream_error_propagated(mock_client_cls, token_env):
    """Non-200 file download responses are forwarded as-is."""
    fake_resp = _fake_upstream_response(status=404, body=b"Not Found")
    mock_client_cls.return_value = _make_mock_client(fake_resp)

    response = client.get(f"/telegram/file/bot{BOT_ALIAS}/documents/missing.pdf")
    assert response.status_code == 404


@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
def test_file_client_closed_after_stream(mock_client_cls, token_env):
    """The httpx client and response are closed after file body is consumed."""
    fake_resp = _fake_upstream_response()
    mock_client = _make_mock_client(fake_resp)
    mock_client_cls.return_value = mock_client

    client.get(f"/telegram/file/bot{BOT_ALIAS}/documents/file.pdf")

    fake_resp.aclose.assert_awaited_once()
    mock_client.aclose.assert_awaited_once()
