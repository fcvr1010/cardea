"""
Tests for the email client module (cardea.client.email).

Uses respx to mock httpx requests — no server or network needed.
"""

import json

import httpx
import respx
from httpx import Response

from cardea.client.email import (
    delete_message,
    list_messages,
    read_message,
    reply_email,
    send_email,
)

BASE = "http://localhost:8000"


# -- list_messages -----------------------------------------------------------


@respx.mock
def test_list_messages_default():
    """list_messages() with defaults sends GET /email/messages."""
    route = respx.get(f"{BASE}/email/messages").mock(
        return_value=Response(200, json=[{"id": "1", "subject": "Hello"}])
    )
    result = list_messages()
    assert result == [{"id": "1", "subject": "Hello"}]
    assert route.called


@respx.mock
def test_list_messages_with_query():
    """list_messages(query=...) passes 'q' as a query parameter."""
    route = respx.get(f"{BASE}/email/messages", params={"q": "UNSEEN"}).mock(
        return_value=Response(200, json=[])
    )
    result = list_messages(query="UNSEEN")
    assert result == []
    assert route.called


@respx.mock
def test_list_messages_with_limit():
    """list_messages(limit=5) passes 'max' as a query parameter."""
    route = respx.get(f"{BASE}/email/messages", params={"max": 5}).mock(
        return_value=Response(200, json=[{"id": "1"}])
    )
    result = list_messages(limit=5)
    assert len(result) == 1
    assert route.called


@respx.mock
def test_list_messages_custom_base_url():
    """list_messages with explicit base_url uses that URL."""
    route = respx.get("http://custom:9000/email/messages").mock(
        return_value=Response(200, json=[])
    )
    result = list_messages(base_url="http://custom:9000")
    assert result == []
    assert route.called


# -- read_message ------------------------------------------------------------


@respx.mock
def test_read_message():
    """read_message(42) sends GET /email/messages/42."""
    msg = {"id": "42", "from": "a@b.com", "subject": "Hi", "body": "Hello"}
    route = respx.get(f"{BASE}/email/messages/42").mock(
        return_value=Response(200, json=msg)
    )
    result = read_message(42)
    assert result == msg
    assert route.called


@respx.mock
def test_read_message_custom_base_url():
    """read_message with explicit base_url."""
    route = respx.get("http://other:8080/email/messages/10").mock(
        return_value=Response(200, json={"id": "10"})
    )
    result = read_message(10, base_url="http://other:8080")
    assert result["id"] == "10"
    assert route.called


# -- delete_message ----------------------------------------------------------


@respx.mock
def test_delete_message():
    """delete_message(25) sends DELETE /email/messages/25."""
    route = respx.delete(f"{BASE}/email/messages/25").mock(
        return_value=Response(200, json={"deleted": True})
    )
    result = delete_message(25)
    assert result == {"deleted": True}
    assert route.called


@respx.mock
def test_delete_message_custom_base_url():
    """delete_message with explicit base_url."""
    route = respx.delete("http://other:8080/email/messages/10").mock(
        return_value=Response(200, json={"deleted": True})
    )
    result = delete_message(10, base_url="http://other:8080")
    assert result["deleted"] is True
    assert route.called


@respx.mock
def test_delete_message_not_found_raises():
    """delete_message raises httpx.HTTPStatusError on 404."""
    respx.delete(f"{BASE}/email/messages/999").mock(
        return_value=Response(404, json={"detail": "Message not found."})
    )
    try:
        delete_message(999)
        raise AssertionError("Expected HTTPStatusError")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 404


# -- send_email --------------------------------------------------------------


@respx.mock
def test_send_email_minimal():
    """send_email sends POST /email/send with to, subject, body."""
    route = respx.post(f"{BASE}/email/send").mock(
        return_value=Response(200, json={"id": "<msg-id>"})
    )
    result = send_email(to="x@y.com", subject="Hi", body="Hello")
    assert result == {"id": "<msg-id>"}
    assert route.called
    # Verify the request body
    sent = route.calls[0].request
    body_data = json.loads(sent.content)
    assert body_data["to"] == "x@y.com"
    assert body_data["subject"] == "Hi"
    assert body_data["body"] == "Hello"
    assert "cc" not in body_data
    assert "bcc" not in body_data


@respx.mock
def test_send_email_with_cc_bcc():
    """send_email includes cc and bcc when provided."""
    route = respx.post(f"{BASE}/email/send").mock(
        return_value=Response(200, json={"id": "<msg-id>"})
    )
    result = send_email(
        to="a@b.com",
        subject="Test",
        body="Body",
        cc="cc@b.com",
        bcc="bcc@b.com",
    )
    assert result["id"] == "<msg-id>"
    body_data = json.loads(route.calls[0].request.content)
    assert body_data["cc"] == "cc@b.com"
    assert body_data["bcc"] == "bcc@b.com"


# -- reply_email -------------------------------------------------------------


@respx.mock
def test_reply_email():
    """reply_email sends POST /email/reply/{message_id}."""
    route = respx.post(f"{BASE}/email/reply/50").mock(
        return_value=Response(200, json={"id": "<reply-id>"})
    )
    result = reply_email(message_id=50, to="a@b.com", subject="Re: Hi", body="Reply")
    assert result == {"id": "<reply-id>"}
    assert route.called
    body_data = json.loads(route.calls[0].request.content)
    assert body_data["to"] == "a@b.com"
    assert body_data["subject"] == "Re: Hi"
    assert body_data["body"] == "Reply"


# -- Error handling ----------------------------------------------------------


@respx.mock
def test_send_email_raises_on_error():
    """send_email raises httpx.HTTPStatusError on non-2xx."""
    respx.post(f"{BASE}/email/send").mock(
        return_value=Response(502, json={"detail": "SMTP error"})
    )
    try:
        send_email(to="x@y.com", subject="Hi", body="Hello")
        raise AssertionError("Expected HTTPStatusError")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 502
