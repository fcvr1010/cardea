"""
Tests for the Gmail proxy router.

httpx.AsyncClient is mocked so no real network traffic is made.
_get_access_token is patched directly in API-forwarding tests to isolate
the token-refresh logic from the route handler logic.
"""

import base64
import email
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from cardea.app import app

client = TestClient(app)

FAKE_TOKEN = "ya29.fake_access_token"
CRED_ENV = {
    "GMAIL_CLIENT_ID": "fake_client_id",
    "GMAIL_CLIENT_SECRET": "fake_client_secret",
    "GMAIL_REFRESH_TOKEN": "fake_refresh_token",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_response(
    status: int = 200, data: dict[str, object] | None = None, text: str = ""
) -> MagicMock:
    """Build a mock that looks like an httpx response with a JSON body."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data or {}
    resp.text = text
    return resp


def _make_mock_client(
    get_side_effect: list[MagicMock] | None = None,
    post_side_effect: list[MagicMock] | None = None,
) -> AsyncMock:
    """Return an AsyncMock that works as an async context manager."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    return mock_client


@pytest.fixture(autouse=True)
def reset_token_cache():
    """Reset the module-level token cache between tests."""
    import cardea.proxies.gmail as gmail_module

    gmail_module._access_token = None
    gmail_module._token_expiry = 0.0
    yield
    gmail_module._access_token = None
    gmail_module._token_expiry = 0.0


# ── Credential checks ─────────────────────────────────────────────────────────


def test_missing_all_credentials_returns_503():
    """All three credentials absent → 503 naming all three."""
    response = client.get("/gmail/messages")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "GMAIL_CLIENT_ID" in detail
    assert "GMAIL_CLIENT_SECRET" in detail
    assert "GMAIL_REFRESH_TOKEN" in detail


@pytest.mark.parametrize("missing_var", list(CRED_ENV.keys()))
def test_any_missing_credential_returns_503(missing_var):
    """Each individual missing credential triggers a 503 naming that var."""
    partial = {k: v for k, v in CRED_ENV.items() if k != missing_var}
    with patch.dict("os.environ", partial, clear=False):
        response = client.get("/gmail/messages")
    assert response.status_code == 503
    assert missing_var in response.json()["detail"]


# ── Token refresh ────────────────────────────────────────────────────────────


@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_token_refresh_success(mock_client_cls):
    """_get_access_token fetches a new token via OAuth2 when cache is empty."""
    token_resp = _fake_response(
        200, {"access_token": "ya29.new_token", "expires_in": 3600}
    )
    mock_client_cls.return_value = _make_mock_client(post_side_effect=[token_resp])

    # Also mock the list call that follows
    list_resp = _fake_response(200, {})
    mock_client_cls.return_value.get = AsyncMock(side_effect=[list_resp])

    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/gmail/messages")

    assert response.status_code == 200
    # Verify the token endpoint was called with correct credentials
    post_call = mock_client_cls.return_value.post.call_args
    assert "oauth2.googleapis.com/token" in post_call.args[0]
    assert post_call.kwargs["data"]["grant_type"] == "refresh_token"


@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_token_refresh_uses_cache(mock_client_cls):
    """_get_access_token returns cached token when it hasn't expired."""
    import cardea.proxies.gmail as gmail_module

    gmail_module._access_token = "ya29.cached"
    gmail_module._token_expiry = time.monotonic() + 3600  # far in the future

    list_resp = _fake_response(200, {})
    mock_client_cls.return_value = _make_mock_client(get_side_effect=[list_resp])

    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/gmail/messages")

    assert response.status_code == 200
    # No POST call should have been made (no token refresh)
    mock_client_cls.return_value.post.assert_not_awaited()


@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_token_refresh_failure_returns_502(mock_client_cls):
    """Failed token refresh returns 502."""
    token_resp = _fake_response(400, text="invalid_grant")
    mock_client_cls.return_value = _make_mock_client(post_side_effect=[token_resp])

    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/gmail/messages")

    assert response.status_code == 502
    assert "Failed to refresh" in response.json()["detail"]


# ── Body extraction ──────────────────────────────────────────────────────────


def test_extract_body_multipart_prefers_plain():
    """_extract_body prefers text/plain over text/html in multipart."""
    from cardea.proxies.gmail import _extract_body

    plain = base64.urlsafe_b64encode(b"plain text").decode()
    html = base64.urlsafe_b64encode(b"<p>html</p>").decode()
    payload = {
        "body": {},
        "parts": [
            {"mimeType": "text/html", "body": {"data": html}},
            {"mimeType": "text/plain", "body": {"data": plain}},
        ],
    }
    assert _extract_body(payload) == "plain text"


def test_extract_body_falls_back_to_html():
    """_extract_body falls back to text/html when no text/plain is present."""
    from cardea.proxies.gmail import _extract_body

    html = base64.urlsafe_b64encode(b"<p>html only</p>").decode()
    payload = {
        "body": {},
        "parts": [
            {"mimeType": "text/html", "body": {"data": html}},
        ],
    }
    assert _extract_body(payload) == "<p>html only</p>"


def test_extract_body_nested_multipart():
    """_extract_body handles nested multipart recursively."""
    from cardea.proxies.gmail import _extract_body

    plain = base64.urlsafe_b64encode(b"nested plain").decode()
    payload = {
        "body": {},
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "body": {},
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": plain}},
                ],
            },
        ],
    }
    assert _extract_body(payload) == "nested plain"


def test_extract_body_empty_payload():
    """_extract_body returns empty string for empty payload."""
    from cardea.proxies.gmail import _extract_body

    assert _extract_body({"body": {}}) == ""


# ── GET /gmail/messages ───────────────────────────────────────────────────────


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_list_messages_forwards_correct_url_and_auth(mock_client_cls, mock_get_token):
    """GET /gmail/messages calls the Gmail API with Bearer token and query params."""
    mock_get_token.return_value = FAKE_TOKEN

    list_resp = _fake_response(200, {"messages": [{"id": "abc123", "threadId": "t1"}]})
    meta_resp = _fake_response(
        200,
        {
            "id": "abc123",
            "threadId": "t1",
            "snippet": "Hello there",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Date", "value": "Mon, 1 Jan 2024 12:00:00 +0000"},
                ]
            },
        },
    )
    mock_client_cls.return_value = _make_mock_client(
        get_side_effect=[list_resp, meta_resp]
    )

    response = client.get("/gmail/messages?q=in:inbox&max=5")

    assert response.status_code == 200
    first_call = mock_client_cls.return_value.get.call_args_list[0]
    assert "messages" in first_call.args[0]
    assert first_call.kwargs["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert first_call.kwargs["params"]["q"] == "in:inbox"
    assert first_call.kwargs["params"]["maxResults"] == 5


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_list_messages_returns_parsed_fields(mock_client_cls, mock_get_token):
    """List response includes id, threadId, subject, from, date, snippet."""
    mock_get_token.return_value = FAKE_TOKEN

    list_resp = _fake_response(200, {"messages": [{"id": "abc123", "threadId": "t1"}]})
    meta_resp = _fake_response(
        200,
        {
            "id": "abc123",
            "threadId": "t1",
            "snippet": "Hello there",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Date", "value": "Mon, 1 Jan 2024 12:00:00 +0000"},
                ]
            },
        },
    )
    mock_client_cls.return_value = _make_mock_client(
        get_side_effect=[list_resp, meta_resp]
    )

    response = client.get("/gmail/messages")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    msg = data[0]
    assert msg["id"] == "abc123"
    assert msg["threadId"] == "t1"
    assert msg["subject"] == "Test Subject"
    assert msg["from"] == "sender@example.com"
    assert msg["snippet"] == "Hello there"


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_list_messages_empty_inbox(mock_client_cls, mock_get_token):
    """Empty messages list returns an empty array without errors."""
    mock_get_token.return_value = FAKE_TOKEN
    mock_client_cls.return_value = _make_mock_client(
        get_side_effect=[_fake_response(200, {})]
    )

    response = client.get("/gmail/messages")

    assert response.status_code == 200
    assert response.json() == []


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_list_messages_upstream_error(mock_client_cls, mock_get_token):
    """Non-200 from the list call is forwarded as an error."""
    mock_get_token.return_value = FAKE_TOKEN
    mock_client_cls.return_value = _make_mock_client(
        get_side_effect=[_fake_response(500, text="Internal Server Error")]
    )

    response = client.get("/gmail/messages")
    assert response.status_code == 500


# ── GET /gmail/messages/{id} ──────────────────────────────────────────────────


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_get_message_returns_parsed_fields(mock_client_cls, mock_get_token):
    """GET /gmail/messages/{id} returns all header fields and decoded body."""
    mock_get_token.return_value = FAKE_TOKEN

    body_text = "Hello, this is the email body."
    encoded_body = base64.urlsafe_b64encode(body_text.encode()).decode()

    full_resp = _fake_response(
        200,
        {
            "id": "msg123",
            "threadId": "thread456",
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "To", "value": "bob@example.com"},
                    {"name": "Cc", "value": "carol@example.com"},
                    {"name": "Subject", "value": "Hello"},
                    {"name": "Date", "value": "Tue, 2 Jan 2024 10:00:00 +0000"},
                ],
                "body": {"data": encoded_body},
            },
        },
    )
    mock_client_cls.return_value = _make_mock_client(
        get_side_effect=[full_resp],
        post_side_effect=[_fake_response(200, {})],
    )

    response = client.get("/gmail/messages/msg123")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "msg123"
    assert data["threadId"] == "thread456"
    assert data["from"] == "alice@example.com"
    assert data["to"] == "bob@example.com"
    assert data["cc"] == "carol@example.com"
    assert data["subject"] == "Hello"
    assert data["body"] == body_text


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_get_message_marks_as_read(mock_client_cls, mock_get_token):
    """GET /gmail/messages/{id} calls modify to remove the UNREAD label."""
    mock_get_token.return_value = FAKE_TOKEN

    full_resp = _fake_response(
        200,
        {
            "id": "msg123",
            "threadId": "thread456",
            "payload": {"headers": [], "body": {"data": ""}},
        },
    )
    mock_client_cls.return_value = _make_mock_client(
        get_side_effect=[full_resp],
        post_side_effect=[_fake_response(200, {})],
    )

    client.get("/gmail/messages/msg123")

    post_call = mock_client_cls.return_value.post.call_args
    assert "msg123/modify" in post_call.args[0]
    assert post_call.kwargs["json"] == {"removeLabelIds": ["UNREAD"]}


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_get_message_upstream_error_propagated(mock_client_cls, mock_get_token):
    """Non-200 from Gmail is forwarded as the same status code."""
    mock_get_token.return_value = FAKE_TOKEN
    mock_client_cls.return_value = _make_mock_client(
        get_side_effect=[_fake_response(404, text="Not Found")]
    )

    response = client.get("/gmail/messages/nonexistent")
    assert response.status_code == 404


# ── POST /gmail/send ──────────────────────────────────────────────────────────


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_send_email_builds_mime_and_calls_send_endpoint(
    mock_client_cls, mock_get_token
):
    """POST /gmail/send constructs a base64url MIME message and posts to Gmail."""
    mock_get_token.return_value = FAKE_TOKEN
    send_resp = _fake_response(200, {"id": "sent123", "threadId": "t_new"})
    mock_client_cls.return_value = _make_mock_client(post_side_effect=[send_resp])

    response = client.post(
        "/gmail/send",
        json={
            "to": "recipient@example.com",
            "subject": "Hello from Cardea",
            "body": "Test body content.",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "sent123"

    post_call = mock_client_cls.return_value.post.call_args
    assert "messages/send" in post_call.args[0]
    assert post_call.kwargs["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    raw = post_call.kwargs["json"]["raw"]
    decoded = base64.urlsafe_b64decode(raw).decode()
    msg = email.message_from_string(decoded)
    assert msg["To"] == "recipient@example.com"
    assert msg["Subject"] == "Hello from Cardea"


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_send_email_with_cc_and_bcc(mock_client_cls, mock_get_token):
    """CC and BCC fields are included in the MIME message when provided."""
    mock_get_token.return_value = FAKE_TOKEN
    mock_client_cls.return_value = _make_mock_client(
        post_side_effect=[_fake_response(200, {"id": "s1", "threadId": "t1"})]
    )

    client.post(
        "/gmail/send",
        json={
            "to": "to@example.com",
            "subject": "CC test",
            "body": "body",
            "cc": "cc@example.com",
            "bcc": "bcc@example.com",
        },
    )

    raw = mock_client_cls.return_value.post.call_args.kwargs["json"]["raw"]
    decoded = base64.urlsafe_b64decode(raw).decode()
    msg = email.message_from_string(decoded)
    assert msg["Cc"] == "cc@example.com"
    assert msg["Bcc"] == "bcc@example.com"


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_send_email_upstream_error_propagated(mock_client_cls, mock_get_token):
    """Non-200/202 from Gmail send is forwarded as an error."""
    mock_get_token.return_value = FAKE_TOKEN
    mock_client_cls.return_value = _make_mock_client(
        post_side_effect=[_fake_response(403, text="Forbidden")]
    )

    response = client.post(
        "/gmail/send",
        json={"to": "x@example.com", "subject": "test", "body": "body"},
    )
    assert response.status_code == 403


# ── POST /gmail/reply/{thread_id} ─────────────────────────────────────────────


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_reply_sets_thread_id_and_in_reply_to(mock_client_cls, mock_get_token):
    """POST /gmail/reply/{thread_id} sends threadId and In-Reply-To in the request."""
    mock_get_token.return_value = FAKE_TOKEN
    mock_client_cls.return_value = _make_mock_client(
        post_side_effect=[
            _fake_response(200, {"id": "reply1", "threadId": "thread_abc"})
        ]
    )

    response = client.post(
        "/gmail/reply/thread_abc",
        json={
            "to": "original@example.com",
            "subject": "Re: Original",
            "body": "Reply body.",
            "message_id": "<original-msg-id@mail.example.com>",
        },
    )

    assert response.status_code == 200

    post_call = mock_client_cls.return_value.post.call_args
    sent_json = post_call.kwargs["json"]

    assert sent_json["threadId"] == "thread_abc"

    raw = sent_json["raw"]
    decoded = base64.urlsafe_b64decode(raw).decode()
    msg = email.message_from_string(decoded)
    assert msg["In-Reply-To"] == "<original-msg-id@mail.example.com>"
    assert msg["References"] == "<original-msg-id@mail.example.com>"


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_reply_without_message_id(mock_client_cls, mock_get_token):
    """Reply without message_id omits In-Reply-To header."""
    mock_get_token.return_value = FAKE_TOKEN
    mock_client_cls.return_value = _make_mock_client(
        post_side_effect=[_fake_response(200, {"id": "r1", "threadId": "t1"})]
    )

    client.post(
        "/gmail/reply/t1",
        json={"to": "x@example.com", "subject": "Re:", "body": "ok"},
    )

    raw = mock_client_cls.return_value.post.call_args.kwargs["json"]["raw"]
    decoded = base64.urlsafe_b64decode(raw).decode()
    msg = email.message_from_string(decoded)
    assert msg["In-Reply-To"] is None


@patch("cardea.proxies.gmail._get_access_token", new_callable=AsyncMock)
@patch("cardea.proxies.gmail.httpx.AsyncClient")
def test_reply_upstream_error_propagated(mock_client_cls, mock_get_token):
    """Non-200/202 from Gmail reply is forwarded as an error."""
    mock_get_token.return_value = FAKE_TOKEN
    mock_client_cls.return_value = _make_mock_client(
        post_side_effect=[_fake_response(429, text="Rate limited")]
    )

    response = client.post(
        "/gmail/reply/thread1",
        json={"to": "x@example.com", "subject": "Re:", "body": "ok"},
    )
    assert response.status_code == 429
