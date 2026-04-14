"""
Email proxy (IMAP/SMTP).

Uses standard IMAP (port 993, SSL) and SMTP (port 587, STARTTLS) with an
App Password, replacing the Gmail OAuth2 flow and its refresh-token expiry
headaches.

Configuration (config.toml ``[email]`` section):
  address      -- email address (e.g. ``agent@example.com``)
  imap_server  -- IMAP host   (e.g. ``imap.gmail.com``)
  smtp_server  -- SMTP host   (e.g. ``smtp.gmail.com``)

Secret:
  cardea_email_password -- App Password (via ``cardea.secrets.get_secret``)

Endpoints
---------
GET    /messages              List messages matching an IMAP SEARCH query.
GET    /messages/{message_id} Fetch a full message by UID; marks it as read.
DELETE /messages/{message_id} Permanently delete a message by UID (IMAP expunge).
POST   /send                  Send a new email via SMTP.
POST   /reply/{message_id}    Reply to an existing message (sets In-Reply-To).
"""

import email as email_pkg
import email.utils
import imaplib
import logging
import os
import smtplib
import tomllib
from email.header import decode_header
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cardea.secrets import get_secret

logger = logging.getLogger(__name__)

PREFIX = "/email"
TAG = "Email"

router = APIRouter()

CONFIG_PATH = (
    Path(os.environ["CARDEA_CONFIG"])
    if os.environ.get("CARDEA_CONFIG")
    else Path(__file__).resolve().parent.parent.parent.parent / "config.toml"
)


# -- Configuration -----------------------------------------------------------


def _load_email_config() -> dict[str, str]:
    """Return the ``[email]`` section from config.toml, or raise 503."""
    if not CONFIG_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="config.toml not found -- email module is not configured.",
        )
    with open(CONFIG_PATH, "rb") as f:
        section: dict[str, str] = tomllib.load(f).get("email", {})
    missing = [k for k in ("address", "imap_server", "smtp_server") if k not in section]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                "Email configuration incomplete in config.toml [email] section. "
                f"Missing keys: {', '.join(missing)}"
            ),
        )
    return section


def _get_password() -> str:
    """Read the app password secret, raising 503 if missing."""
    try:
        return get_secret("cardea_email_password")
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail=(
                "Email password not configured. "
                "Provide cardea_email_password as a secret or environment variable."
            ),
        )


# -- IMAP helpers -------------------------------------------------------------


def _imap_connect(cfg: dict[str, str], password: str) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP connection to the configured server."""
    try:
        conn = imaplib.IMAP4_SSL(cfg["imap_server"], 993)
        conn.login(cfg["address"], password)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to connect to IMAP server: {exc}",
        )
    return conn


def _decode_header_value(raw: str | None) -> str:
    """Decode an RFC 2047 encoded header value into a plain string."""
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded_parts: list[str] = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded_parts.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(data)
    return "".join(decoded_parts)


def _extract_body(msg: email_pkg.message.Message) -> str:
    """Extract the plain-text body from an email message.

    Prefers text/plain; falls back to text/html.
    """
    if not msg.is_multipart():
        raw_payload = msg.get_payload(decode=True)
        if not isinstance(raw_payload, bytes):
            return ""
        charset = msg.get_content_charset() or "utf-8"
        return raw_payload.decode(charset, errors="replace")

    plain: str | None = None
    html: str | None = None
    for part in msg.walk():
        content_type = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        raw_payload = part.get_payload(decode=True)
        if not isinstance(raw_payload, bytes):
            continue
        charset = part.get_content_charset() or "utf-8"
        text = raw_payload.decode(charset, errors="replace")
        if content_type == "text/plain" and plain is None:
            plain = text
        elif content_type == "text/html" and html is None:
            html = text
    return plain or html or ""


# -- SMTP helpers -------------------------------------------------------------


def _smtp_send(cfg: dict[str, str], password: str, msg: MIMEText) -> str:
    """Send a MIMEText message via SMTP with STARTTLS.  Returns Message-ID."""
    msg["From"] = cfg["address"]

    recipients: list[str] = []
    for field in ("To", "Cc", "Bcc"):
        value = msg[field]
        if value:
            recipients.extend(addr for _, addr in email_pkg.utils.getaddresses([value]))

    # Remove Bcc from headers before sending (standard practice).
    if "Bcc" in msg:
        del msg["Bcc"]

    try:
        with smtplib.SMTP(cfg["smtp_server"], 587) as smtp:
            smtp.starttls()
            smtp.login(cfg["address"], password)
            smtp.sendmail(cfg["address"], recipients, msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to send email via SMTP: {exc}",
        )

    return msg["Message-ID"] or ""


# -- List messages ------------------------------------------------------------


@router.get("/messages")
async def list_messages(q: str = "", max: int = 10) -> list[dict[str, Any]]:
    """List messages matching an IMAP SEARCH query.

    The *q* parameter accepts raw IMAP SEARCH syntax, e.g.
    ``FROM "sender" UNSEEN SUBJECT "test"``.  Defaults to ``ALL``.
    """
    cfg = _load_email_config()
    password = _get_password()
    conn = _imap_connect(cfg, password)
    try:
        conn.select("INBOX", readonly=True)
        search_criteria = q.strip() if q.strip() else "ALL"

        try:
            _status, data = conn.uid("SEARCH", None, search_criteria)  # type: ignore[arg-type]
        except imaplib.IMAP4.error as exc:
            raise HTTPException(status_code=400, detail=f"IMAP SEARCH failed: {exc}")

        uids: list[bytes] = data[0].split() if data[0] else []
        # Most recent first, limited to *max*.
        uids = list(reversed(uids))[:max]

        results: list[dict[str, Any]] = []
        for uid in uids:
            uid_str = uid.decode()
            _status, msg_data = conn.uid(
                "FETCH", uid_str, "(BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.200>)"
            )
            if not msg_data or msg_data[0] is None:
                continue

            # IMAP may return HEADER and TEXT tuples in any order.
            # Identify each by inspecting the descriptor bytes.
            raw_header = b""
            raw_snippet_bytes = b""
            for item in msg_data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                descriptor = item[0]
                if not isinstance(descriptor, bytes):
                    continue
                if b"HEADER" in descriptor:
                    raw_header = item[1] if isinstance(item[1], bytes) else b""
                elif b"TEXT" in descriptor:
                    raw_snippet_bytes = item[1] if isinstance(item[1], bytes) else b""
            msg = email_pkg.message_from_bytes(raw_header)

            # Snippet from partial body fetch.
            snippet = ""
            if raw_snippet_bytes:
                snippet = raw_snippet_bytes.decode("utf-8", errors="replace").strip()

            results.append(
                {
                    "id": uid_str,
                    "subject": _decode_header_value(msg["Subject"]),
                    "from": _decode_header_value(msg["From"]),
                    "date": msg["Date"] or "",
                    "snippet": snippet[:200],
                }
            )
        return results
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# -- Get message --------------------------------------------------------------


@router.get("/messages/{message_id}")
async def get_message(message_id: str) -> dict[str, Any]:
    """Fetch a full message by IMAP UID and mark it as read."""
    cfg = _load_email_config()
    password = _get_password()
    conn = _imap_connect(cfg, password)
    try:
        conn.select("INBOX")
        _status, msg_data = conn.uid("FETCH", message_id, "(RFC822)")

        if not msg_data or msg_data[0] is None:
            raise HTTPException(status_code=404, detail="Message not found.")

        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
        if not raw:
            raise HTTPException(status_code=404, detail="Message not found.")

        msg = email_pkg.message_from_bytes(raw)

        # Mark as read.
        conn.uid("STORE", message_id, "+FLAGS", "(\\Seen)")

        return {
            "id": message_id,
            "from": _decode_header_value(msg["From"]),
            "to": _decode_header_value(msg["To"]),
            "cc": _decode_header_value(msg.get("Cc")),
            "subject": _decode_header_value(msg["Subject"]),
            "date": msg["Date"] or "",
            "body": _extract_body(msg),
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# -- Delete message -----------------------------------------------------------


@router.delete("/messages/{message_id}")
async def delete_message(message_id: str) -> dict[str, bool]:
    """Permanently delete a message by IMAP UID.

    Sets the ``\\Deleted`` flag and expunges the mailbox.
    """
    cfg = _load_email_config()
    password = _get_password()
    conn = _imap_connect(cfg, password)
    try:
        conn.select("INBOX")

        # Skip existence pre-check — IMAP FLAGS responses vary across servers
        # (plain bytes vs. tuple with None) making reliable detection fragile.
        # Instead, attempt STORE + EXPUNGE directly and let IMAP errors surface.
        try:
            status, _data = conn.uid("STORE", message_id, "+FLAGS", "(\\Deleted)")
            if status != "OK":
                raise HTTPException(status_code=404, detail="Message not found.")
            conn.uid("EXPUNGE", message_id)
        except HTTPException:
            raise
        except imaplib.IMAP4.error:
            raise HTTPException(status_code=404, detail="Message not found.")
        return {"deleted": True}
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# -- Send email ---------------------------------------------------------------


class SendRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: str | None = None
    bcc: str | None = None


@router.post("/send")
async def send_email(req: SendRequest) -> dict[str, str]:
    """Send a new email via SMTP."""
    cfg = _load_email_config()
    password = _get_password()

    msg = MIMEText(req.body, "plain", "utf-8")
    msg["To"] = req.to
    msg["Subject"] = req.subject
    msg["Message-ID"] = email_pkg.utils.make_msgid()
    if req.cc:
        msg["Cc"] = req.cc
    if req.bcc:
        msg["Bcc"] = req.bcc

    message_id = _smtp_send(cfg, password, msg)
    return {"id": message_id}


# -- Reply --------------------------------------------------------------------


class ReplyRequest(BaseModel):
    to: str
    subject: str
    body: str


@router.post("/reply/{message_id}")
async def reply_email(message_id: str, req: ReplyRequest) -> dict[str, str]:
    """Reply to an existing message.

    Fetches the original via IMAP to extract its Message-ID and References
    headers, then sends a reply with ``In-Reply-To`` and ``References`` set.
    """
    cfg = _load_email_config()
    password = _get_password()

    # Fetch original to get its Message-ID and References headers.
    conn = _imap_connect(cfg, password)
    try:
        conn.select("INBOX", readonly=True)
        _status, msg_data = conn.uid(
            "FETCH",
            message_id,
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES)])",
        )
        if not msg_data or msg_data[0] is None:
            raise HTTPException(status_code=404, detail="Original message not found.")
        raw_header = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
        original = email_pkg.message_from_bytes(raw_header)
        original_msg_id: str = original["Message-ID"] or ""
        original_refs: str = original.get("References", "") or ""
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    msg = MIMEText(req.body, "plain", "utf-8")
    msg["To"] = req.to
    msg["Subject"] = req.subject
    msg["Message-ID"] = email_pkg.utils.make_msgid()
    if original_msg_id:
        msg["In-Reply-To"] = original_msg_id
        refs = original_refs.strip()
        if refs:
            msg["References"] = f"{refs} {original_msg_id}"
        else:
            msg["References"] = original_msg_id

    sent_id = _smtp_send(cfg, password, msg)
    return {"id": sent_id}
