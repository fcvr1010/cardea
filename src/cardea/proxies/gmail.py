"""
Gmail API proxy.

Credentials are read from three secrets (or environment variables):
  cardea_gmail_client_id      — Google OAuth2 client ID
  cardea_gmail_client_secret  — Google OAuth2 client secret
  cardea_gmail_refresh_token  — Offline refresh token

Access tokens are fetched via the OAuth2 token endpoint and cached with a
60-second safety margin before expiry, so they are refreshed lazily on each
request only when needed.

Endpoints
---------
GET  /messages              List messages matching a Gmail search query.
GET  /messages/{message_id} Fetch a full message (headers + decoded body).
POST /send                  Send a new email (new thread).
POST /reply/{thread_id}     Reply in an existing thread.
"""

import base64
import logging
import time
from email.mime.text import MIMEText
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cardea.secrets import get_secret

logger = logging.getLogger(__name__)

PREFIX = "/gmail"
TAG = "Gmail"

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Module-level token cache.
_access_token: str | None = None
_token_expiry: float = 0.0

router = APIRouter()

_CRED_VARS = (
    "cardea_gmail_client_id",
    "cardea_gmail_client_secret",
    "cardea_gmail_refresh_token",
)


def _check_credentials() -> None:
    """Raise if any required Gmail credential is missing."""
    missing = []
    for var in _CRED_VARS:
        try:
            get_secret(var)
        except RuntimeError:
            missing.append(var)
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                "Gmail credentials not configured. "
                "Provide the following as secrets or environment variables: "
                + ", ".join(missing)
            ),
        )


async def _get_access_token() -> str:
    """Return a valid Gmail access token, refreshing via OAuth2 when needed."""
    global _access_token, _token_expiry

    if _access_token and time.monotonic() < _token_expiry - 60:
        return _access_token

    _check_credentials()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": get_secret("cardea_gmail_client_id"),
                "client_secret": get_secret("cardea_gmail_client_secret"),
                "refresh_token": get_secret("cardea_gmail_refresh_token"),
                "grant_type": "refresh_token",
            },
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to refresh Gmail access token: {resp.text}",
        )

    data = resp.json()
    token: str = data["access_token"]
    _access_token = token
    _token_expiry = time.monotonic() + data.get("expires_in", 3600)
    return token


def _gmail_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _build_message(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    in_reply_to: str | None = None,
) -> str:
    """Return a base64url-encoded RFC 2822 MIME message."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _extract_body(payload: dict[str, Any]) -> str:
    """Decode the plain-text body from a Gmail message payload.

    Prefers text/plain; falls back to text/html. Handles simple and
    multipart messages recursively.
    """
    data = payload.get("body", {}).get("data", "")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    plain: str | None = None
    html: str | None = None

    for part in parts:
        mime = part.get("mimeType", "")
        part_data = part.get("body", {}).get("data", "")
        if part_data:
            decoded = base64.urlsafe_b64decode(part_data).decode(
                "utf-8", errors="replace"
            )
            if mime == "text/plain":
                plain = decoded
            elif mime == "text/html":
                html = decoded
        if part.get("parts"):
            nested = _extract_body(part)
            if nested:
                return nested

    return plain or html or ""


# ── List messages ─────────────────────────────────────────────────────────────


@router.get("/messages")
async def list_messages(q: str = "in:inbox", max: int = 10) -> list[dict[str, Any]]:
    """List messages matching *q*; returns id/threadId/subject/from/date/snippet."""
    token = await _get_access_token()
    headers = _gmail_headers(token)

    async with httpx.AsyncClient() as client:
        list_resp = await client.get(
            f"{GMAIL_API_BASE}/messages",
            headers=headers,
            params={"q": q, "maxResults": max},
        )
        if list_resp.status_code != 200:
            raise HTTPException(
                status_code=list_resp.status_code, detail=list_resp.text
            )

        ids = [m["id"] for m in list_resp.json().get("messages", [])]

        results = []
        for msg_id in ids:
            meta_resp = await client.get(
                f"{GMAIL_API_BASE}/messages/{msg_id}",
                headers=headers,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                },
            )
            if meta_resp.status_code == 200:
                meta = meta_resp.json()
                hdrs = {
                    h["name"]: h["value"]
                    for h in meta.get("payload", {}).get("headers", [])
                }
                results.append(
                    {
                        "id": meta["id"],
                        "threadId": meta["threadId"],
                        "subject": hdrs.get("Subject", ""),
                        "from": hdrs.get("From", ""),
                        "date": hdrs.get("Date", ""),
                        "snippet": meta.get("snippet", ""),
                    }
                )

    return results


# ── Get message ───────────────────────────────────────────────────────────────


@router.get("/messages/{message_id}")
async def get_message(message_id: str) -> dict[str, Any]:
    """Return full headers and decoded body for a single message."""
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GMAIL_API_BASE}/messages/{message_id}",
            headers=_gmail_headers(token),
            params={"format": "full"},
        )

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await client.post(
            f"{GMAIL_API_BASE}/messages/{message_id}/modify",
            headers=_gmail_headers(token),
            json={"removeLabelIds": ["UNREAD"]},
        )

    data = resp.json()
    payload = data.get("payload", {})
    hdrs = {h["name"]: h["value"] for h in payload.get("headers", [])}

    return {
        "id": data["id"],
        "threadId": data["threadId"],
        "from": hdrs.get("From", ""),
        "to": hdrs.get("To", ""),
        "cc": hdrs.get("Cc", ""),
        "subject": hdrs.get("Subject", ""),
        "date": hdrs.get("Date", ""),
        "body": _extract_body(payload),
    }


# ── Send email ────────────────────────────────────────────────────────────────


class SendRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: str | None = None
    bcc: str | None = None


@router.post("/send")
async def send_email(req: SendRequest) -> dict[str, Any]:
    """Send a new email (starts a new thread)."""
    token = await _get_access_token()
    raw = _build_message(req.to, req.subject, req.body, req.cc, req.bcc)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GMAIL_API_BASE}/messages/send",
            headers=_gmail_headers(token),
            json={"raw": raw},
        )

    if resp.status_code not in (200, 202):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    result: dict[str, Any] = resp.json()
    return result


# ── Reply ─────────────────────────────────────────────────────────────────────


class ReplyRequest(BaseModel):
    to: str
    subject: str
    body: str
    message_id: str | None = None


@router.post("/reply/{thread_id}")
async def reply_email(thread_id: str, req: ReplyRequest) -> dict[str, Any]:
    """Reply in an existing thread; sets In-Reply-To when message_id is given."""
    token = await _get_access_token()
    raw = _build_message(req.to, req.subject, req.body, in_reply_to=req.message_id)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GMAIL_API_BASE}/messages/send",
            headers=_gmail_headers(token),
            json={"raw": raw, "threadId": thread_id},
        )

    if resp.status_code not in (200, 202):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    result: dict[str, Any] = resp.json()
    return result
