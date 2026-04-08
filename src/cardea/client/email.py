"""
Email client for Cardea — list, read, send, and reply to emails.

    list_messages(query="ALL", limit=10, base_url=None) -> list[dict]
    read_message(message_id, base_url=None) -> dict
    delete_message(message_id, base_url=None) -> dict
    send_email(to, subject, body, cc=None, bcc=None, base_url=None) -> dict
    reply_email(message_id, to, subject, body, base_url=None) -> dict

Server endpoints (under ``/email``):

- ``GET    /messages``              — list messages (IMAP SEARCH)
- ``GET    /messages/{message_id}`` — fetch a full message by UID
- ``DELETE /messages/{message_id}`` — delete a message by UID
- ``POST   /send``                  — send a new email
- ``POST   /reply/{message_id}``    — reply to an existing message

Each item from ``list_messages``: ``{id, subject, from, date, snippet}``

``read_message`` returns: ``{id, from, to, cc, subject, date, body}``

``delete_message`` returns: ``{id}`` (the IMAP UID of the deleted message)

``send_email`` / ``reply_email`` return: ``{id}`` (the Message-ID)

The ``query`` parameter uses IMAP SEARCH syntax, e.g.::

    "UNSEEN"
    "FROM \\"someone@example.com\\""
    "SUBJECT \\"invoice\\""
    "UNSEEN FROM \\"someone@example.com\\""
"""

from __future__ import annotations

from typing import Any

from cardea.client._base import _request, _resolve_base_url


def list_messages(
    query: str = "ALL",
    limit: int = 10,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """List email messages matching an IMAP SEARCH query.

    Args:
        query: IMAP SEARCH expression (default ``"ALL"``).
        limit: Maximum number of messages to return (default 10).
        base_url: Override the Cardea server URL.

    Returns:
        List of message summaries, each with keys:
        ``id``, ``subject``, ``from``, ``date``, ``snippet``.
    """
    url = _resolve_base_url(base_url)
    params: dict[str, str | int] = {}
    if query and query != "ALL":
        params["q"] = query
    if limit != 10:
        params["max"] = limit
    response = _request("GET", f"{url}/email/messages", params=params, timeout=60.0)
    result: list[dict[str, Any]] = response.json()
    return result


def read_message(
    message_id: int,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Fetch a full email message by IMAP UID.

    The message is automatically marked as read on the server.

    Args:
        message_id: IMAP UID of the message.
        base_url: Override the Cardea server URL.

    Returns:
        Message dict with keys:
        ``id``, ``from``, ``to``, ``cc``, ``subject``, ``date``, ``body``.
    """
    url = _resolve_base_url(base_url)
    response = _request("GET", f"{url}/email/messages/{message_id}")
    result: dict[str, Any] = response.json()
    return result


def delete_message(
    message_id: int,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Delete an email message by IMAP UID.

    The message is flagged as deleted and expunged from the mailbox.

    Args:
        message_id: IMAP UID of the message to delete.
        base_url: Override the Cardea server URL.

    Returns:
        Dict with ``id`` key containing the UID of the deleted message.
    """
    url = _resolve_base_url(base_url)
    response = _request("DELETE", f"{url}/email/messages/{message_id}")
    result: dict[str, Any] = response.json()
    return result


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Send a new email via Cardea's SMTP proxy.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body.
        cc: CC recipient(s), optional.
        bcc: BCC recipient(s), optional.
        base_url: Override the Cardea server URL.

    Returns:
        Dict with ``id`` key containing the Message-ID of the sent email.
    """
    url = _resolve_base_url(base_url)
    payload: dict[str, Any] = {"to": to, "subject": subject, "body": body}
    if cc:
        payload["cc"] = cc
    if bcc:
        payload["bcc"] = bcc
    response = _request("POST", f"{url}/email/send", json=payload)
    result: dict[str, Any] = response.json()
    return result


def reply_email(
    message_id: int,
    to: str,
    subject: str,
    body: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Reply to an existing email by IMAP UID.

    The server sets ``In-Reply-To`` and ``References`` headers
    automatically based on the original message.

    Args:
        message_id: IMAP UID of the message being replied to.
        to: Recipient email address for the reply.
        subject: Subject line (typically ``"Re: ..."``).
        body: Plain-text reply body.
        base_url: Override the Cardea server URL.

    Returns:
        Dict with ``id`` key containing the Message-ID of the sent reply.
    """
    url = _resolve_base_url(base_url)
    payload: dict[str, Any] = {"to": to, "subject": subject, "body": body}
    response = _request("POST", f"{url}/email/reply/{message_id}", json=payload)
    result: dict[str, Any] = response.json()
    return result
