"""
Tests for the Email proxy router (IMAP/SMTP).

imaplib and smtplib are mocked so no real network traffic is made.
"""

import email as email_pkg
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cardea.app import app

client = TestClient(app)

CRED_ENV = {"cardea_email_password": "fake_app_password"}

EMAIL_CONFIG = {
    "address": "test@example.com",
    "imap_server": "imap.example.com",
    "smtp_server": "smtp.example.com",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_raw_email(
    subject: str = "Test Subject",
    from_addr: str = "sender@example.com",
    to_addr: str = "recipient@example.com",
    cc_addr: str | None = None,
    body: str = "Hello, this is the body.",
    message_id: str = "<test-msg-id@example.com>",
) -> bytes:
    """Build a raw RFC 2822 email as bytes."""
    from email.mime.text import MIMEText

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Date"] = "Mon, 1 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = message_id
    if cc_addr:
        msg["Cc"] = cc_addr
    return msg.as_bytes()


def _build_header_bytes(
    subject: str = "Test Subject",
    from_addr: str = "sender@example.com",
    date: str = "Mon, 1 Jan 2024 12:00:00 +0000",
) -> bytes:
    """Build raw header bytes (no body) for IMAP FETCH HEADER responses."""
    lines = [
        f"Subject: {subject}",
        f"From: {from_addr}",
        f"Date: {date}",
        "",
    ]
    return "\r\n".join(lines).encode()


def _mock_imap(
    search_uids: list[bytes] | None = None,
    fetch_responses: dict[str, list[Any]] | None = None,
    login_error: bool = False,
) -> MagicMock:
    """Build a mock IMAP4_SSL connection.

    *search_uids*: list of UID byte strings returned by SEARCH.
    *fetch_responses*: maps fetch data item string patterns to response data.
    """
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])

    if login_error:
        conn.login.side_effect = Exception("Authentication failed")

    uid_data = b" ".join(search_uids) if search_uids else b""
    conn.uid = MagicMock()

    def uid_side_effect(command: str, *args: object) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            return ("OK", [uid_data])
        if command == "FETCH":
            uid_val = args[0] if args else b""
            uid_str = uid_val.decode() if isinstance(uid_val, bytes) else str(uid_val)
            if fetch_responses and uid_str in fetch_responses:
                return ("OK", fetch_responses[uid_str])
            not_found: list[Any] = [None]
            return ("OK", not_found)
        if command == "STORE":
            return ("OK", [b""])
        return ("OK", [])

    conn.uid.side_effect = uid_side_effect
    conn.logout.return_value = ("BYE", [])
    return conn


def _mock_smtp() -> MagicMock:
    """Build a mock SMTP connection that works as a context manager."""
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    smtp.starttls.return_value = (220, b"Ready")
    smtp.login.return_value = (235, b"OK")
    smtp.sendmail.return_value = {}
    return smtp


# ── Credential checks ────────────────────────────────────────────────────────


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
def test_missing_password_returns_503(_mock_cfg):
    """Missing cardea_email_password secret returns 503."""
    response = client.get("/email/messages")
    assert response.status_code == 503
    assert "cardea_email_password" in response.json()["detail"]


def test_missing_config_returns_503():
    """Missing config keys in [email] section returns 503."""
    # _load_email_config validates required keys and raises 503 when any are missing.
    from fastapi import HTTPException

    with patch(
        "cardea.proxies.email._load_email_config",
        side_effect=HTTPException(
            status_code=503,
            detail="Email configuration incomplete in config.toml [email] section. "
            "Missing keys: address, imap_server, smtp_server",
        ),
    ):
        with patch.dict("os.environ", CRED_ENV):
            response = client.get("/email/messages")
    assert response.status_code == 503
    assert "config" in response.json()["detail"].lower()


# ── IMAP connection failure ──────────────────────────────────────────────────


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_imap_connection_failure_returns_502(mock_imap_cls, _mock_cfg):
    """IMAP connection error returns 502."""
    mock_imap_cls.side_effect = OSError("Connection refused")
    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/email/messages")
    assert response.status_code == 502
    assert "IMAP" in response.json()["detail"]


# ── GET /email/messages ──────────────────────────────────────────────────────


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_list_messages_returns_parsed_fields(mock_imap_cls, _mock_cfg):
    """GET /email/messages returns id, subject, from, date, snippet."""
    header = _build_header_bytes(subject="Hello", from_addr="alice@example.com")
    snippet = b"This is a preview of the email body"

    imap = _mock_imap(
        search_uids=[b"42"],
        fetch_responses={
            "42": [
                (b"42 (BODY[HEADER] {100}", header),
                (b"42 (BODY[TEXT]<0> {35}", snippet),
                b")",
            ]
        },
    )
    mock_imap_cls.return_value = imap

    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/email/messages?q=ALL&max=5")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    msg = data[0]
    assert msg["id"] == "42"
    assert msg["subject"] == "Hello"
    assert msg["from"] == "alice@example.com"
    assert msg["date"] == "Mon, 1 Jan 2024 12:00:00 +0000"


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_list_messages_empty_inbox(mock_imap_cls, _mock_cfg):
    """Empty SEARCH result returns an empty list."""
    imap = _mock_imap(search_uids=[])
    mock_imap_cls.return_value = imap

    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/email/messages")

    assert response.status_code == 200
    assert response.json() == []


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_list_messages_respects_max(mock_imap_cls, _mock_cfg):
    """Only the most recent *max* messages are returned."""
    header = _build_header_bytes()
    snippet = b"preview"

    fetch_data = {}
    for uid in [b"1", b"2", b"3", b"4", b"5"]:
        uid_str = uid.decode()
        fetch_data[uid_str] = [
            (uid + b" (BODY[HEADER] {100}", header),
            (uid + b" (BODY[TEXT]<0> {7}", snippet),
            b")",
        ]

    imap = _mock_imap(
        search_uids=[b"1", b"2", b"3", b"4", b"5"],
        fetch_responses=fetch_data,
    )
    mock_imap_cls.return_value = imap

    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/email/messages?max=2")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    # Most recent first (reversed order).
    assert data[0]["id"] == "5"
    assert data[1]["id"] == "4"


# ── GET /email/messages/{id} ────────────────────────────────────────────────


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_get_message_returns_full_message(mock_imap_cls, _mock_cfg):
    """GET /email/messages/{id} returns all fields and decoded body."""
    raw = _build_raw_email(
        subject="Full Message",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        cc_addr="carol@example.com",
        body="Full body content.",
    )
    imap = _mock_imap(fetch_responses={"99": [(b"99 (RFC822 {500}", raw), b")"]})
    mock_imap_cls.return_value = imap

    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/email/messages/99")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "99"
    assert data["from"] == "alice@example.com"
    assert data["to"] == "bob@example.com"
    assert data["cc"] == "carol@example.com"
    assert data["subject"] == "Full Message"
    assert data["body"] == "Full body content."


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_get_message_marks_as_read(mock_imap_cls, _mock_cfg):
    """GET /email/messages/{id} calls STORE +FLAGS \\Seen."""
    raw = _build_raw_email()
    imap = _mock_imap(fetch_responses={"10": [(b"10 (RFC822 {500}", raw), b")"]})
    mock_imap_cls.return_value = imap

    with patch.dict("os.environ", CRED_ENV):
        client.get("/email/messages/10")

    # Find the STORE call.
    store_calls = [call for call in imap.uid.call_args_list if call.args[0] == "STORE"]
    assert len(store_calls) == 1
    assert store_calls[0].args[1] == "10"
    assert store_calls[0].args[2] == "+FLAGS"
    assert store_calls[0].args[3] == "(\\Seen)"


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_get_message_not_found_returns_404(mock_imap_cls, _mock_cfg):
    """Fetching a nonexistent UID returns 404."""
    imap = _mock_imap(fetch_responses={})
    mock_imap_cls.return_value = imap

    with patch.dict("os.environ", CRED_ENV):
        response = client.get("/email/messages/999")

    assert response.status_code == 404


# ── POST /email/send ─────────────────────────────────────────────────────────


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.smtplib.SMTP")
def test_send_email_calls_smtp(mock_smtp_cls, _mock_cfg):
    """POST /email/send connects via SMTP with STARTTLS and sends."""
    smtp = _mock_smtp()
    mock_smtp_cls.return_value = smtp

    with patch.dict("os.environ", CRED_ENV):
        response = client.post(
            "/email/send",
            json={
                "to": "recipient@example.com",
                "subject": "Hello",
                "body": "Test body.",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert "id" in data

    # Verify SMTP interactions.
    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("test@example.com", "fake_app_password")

    # Check the sent message.
    sendmail_call = smtp.sendmail.call_args
    assert sendmail_call.args[0] == "test@example.com"
    assert "recipient@example.com" in sendmail_call.args[1]
    raw_msg = sendmail_call.args[2]
    msg = email_pkg.message_from_string(raw_msg)
    assert msg["To"] == "recipient@example.com"
    assert msg["Subject"] == "Hello"
    assert msg["From"] == "test@example.com"


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.smtplib.SMTP")
def test_send_email_with_cc_and_bcc(mock_smtp_cls, _mock_cfg):
    """CC and BCC addresses are included in recipients; BCC is stripped from headers."""
    smtp = _mock_smtp()
    mock_smtp_cls.return_value = smtp

    with patch.dict("os.environ", CRED_ENV):
        client.post(
            "/email/send",
            json={
                "to": "to@example.com",
                "subject": "CC test",
                "body": "body",
                "cc": "cc@example.com",
                "bcc": "bcc@example.com",
            },
        )

    sendmail_call = smtp.sendmail.call_args
    recipients = sendmail_call.args[1]
    assert "to@example.com" in recipients
    assert "cc@example.com" in recipients
    assert "bcc@example.com" in recipients

    # BCC should not appear in the actual message headers.
    raw_msg = sendmail_call.args[2]
    msg = email_pkg.message_from_string(raw_msg)
    assert msg["Bcc"] is None
    assert msg["Cc"] == "cc@example.com"


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.smtplib.SMTP")
def test_send_email_smtp_failure_returns_502(mock_smtp_cls, _mock_cfg):
    """SMTP connection/send failure returns 502."""
    smtp = _mock_smtp()
    import smtplib as _smtplib

    smtp.sendmail.side_effect = _smtplib.SMTPException("Connection lost")
    mock_smtp_cls.return_value = smtp

    with patch.dict("os.environ", CRED_ENV):
        response = client.post(
            "/email/send",
            json={"to": "x@example.com", "subject": "test", "body": "body"},
        )

    assert response.status_code == 502
    assert "SMTP" in response.json()["detail"]


# ── POST /email/reply/{message_id} ──────────────────────────────────────────


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.smtplib.SMTP")
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_reply_sets_in_reply_to_and_references(mock_imap_cls, mock_smtp_cls, _mock_cfg):
    """POST /email/reply/{id} sets In-Reply-To and References headers."""
    # Build a header-only response with a Message-ID.
    header_bytes = b"Message-ID: <original@example.com>\r\n\r\n"
    imap = _mock_imap(
        fetch_responses={"50": [(b"50 (BODY[HEADER] {40}", header_bytes), b")"]}
    )
    mock_imap_cls.return_value = imap

    smtp = _mock_smtp()
    mock_smtp_cls.return_value = smtp

    with patch.dict("os.environ", CRED_ENV):
        response = client.post(
            "/email/reply/50",
            json={
                "to": "original@example.com",
                "subject": "Re: Original",
                "body": "Reply body.",
            },
        )

    assert response.status_code == 200

    raw_msg = smtp.sendmail.call_args.args[2]
    msg = email_pkg.message_from_string(raw_msg)
    assert msg["In-Reply-To"] == "<original@example.com>"
    assert msg["References"] == "<original@example.com>"


@patch("cardea.proxies.email._load_email_config", return_value=EMAIL_CONFIG)
@patch("cardea.proxies.email.imaplib.IMAP4_SSL")
def test_reply_original_not_found_returns_404(mock_imap_cls, _mock_cfg):
    """Replying to a nonexistent message returns 404."""
    imap = _mock_imap(fetch_responses={})
    mock_imap_cls.return_value = imap

    with patch.dict("os.environ", CRED_ENV):
        response = client.post(
            "/email/reply/999",
            json={
                "to": "x@example.com",
                "subject": "Re: test",
                "body": "reply",
            },
        )

    assert response.status_code == 404


# ── Body extraction ──────────────────────────────────────────────────────────


def test_extract_body_plain_text():
    """_extract_body extracts plain text from a simple message."""
    from cardea.proxies.email import _extract_body

    raw = _build_raw_email(body="Simple plain text.")
    msg = email_pkg.message_from_bytes(raw)
    assert _extract_body(msg) == "Simple plain text."


def test_extract_body_multipart_prefers_plain():
    """_extract_body prefers text/plain over text/html in multipart."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from cardea.proxies.email import _extract_body

    mp = MIMEMultipart("alternative")
    mp.attach(MIMEText("<p>html</p>", "html", "utf-8"))
    mp.attach(MIMEText("plain text", "plain", "utf-8"))
    msg = email_pkg.message_from_bytes(mp.as_bytes())
    assert _extract_body(msg) == "plain text"


def test_extract_body_multipart_falls_back_to_html():
    """_extract_body falls back to HTML when no text/plain is present."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from cardea.proxies.email import _extract_body

    mp = MIMEMultipart("alternative")
    mp.attach(MIMEText("<p>only html</p>", "html", "utf-8"))
    msg = email_pkg.message_from_bytes(mp.as_bytes())
    assert _extract_body(msg) == "<p>only html</p>"


# ── Header decoding ─────────────────────────────────────────────────────────


def test_decode_header_value_plain():
    """Plain ASCII header values pass through unchanged."""
    from cardea.proxies.email import _decode_header_value

    assert _decode_header_value("Hello World") == "Hello World"


def test_decode_header_value_none():
    """None returns empty string."""
    from cardea.proxies.email import _decode_header_value

    assert _decode_header_value(None) == ""


def test_decode_header_value_encoded():
    """RFC 2047 encoded headers are decoded correctly."""
    from cardea.proxies.email import _decode_header_value

    # "=?utf-8?B?...?=" is base64-encoded UTF-8
    import base64

    encoded = "=?utf-8?B?" + base64.b64encode("Ciao".encode()).decode() + "?="
    assert _decode_header_value(encoded) == "Ciao"
