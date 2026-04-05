"""
app/ingestion/fetcher.py
========================
PURPOSE:
    The agent's "eyes". Reads emails from Gmail via IMAP
    and converts raw email bytes into clean Python dicts.

    Also provides get_dummy_emails() so you can run and test
    the full pipeline WITHOUT a real Gmail account.

CONCEPT — Ingestion Layer:
    The agent doesn't care WHERE emails come from.
    fetch_unseen_emails() and get_dummy_emails() return
    the exact same dict structure.
    This is called "Dependency Injection" or "test doubles" in interviews —
    swappable sources behind a consistent interface.

IMAP BASICS:
    IMAP = Internet Message Access Protocol
    It lets you READ emails from a server without deleting them.
    We search for UNSEEN (unread) emails each poll cycle.
    Gmail requires an App Password (not your real password).
"""

import imaplib        # built-in: IMAP protocol client (no pip install)
import email          # built-in: email message parser
import logging
from email.header import decode_header   # decodes encoded subject lines

from config import settings

logger = logging.getLogger(__name__)


# ── Private helpers ───────────────────────────────────────────────

def _decode_str(raw) -> str:
    """
    Email headers are often encoded. For example:
        =?UTF-8?B?SGVsbG8gV29ybGQ=?=  →  "Hello World"

    decode_header() handles base64 and quoted-printable encodings.
    We take the first decoded part and convert bytes → string.
    """
    if raw is None:
        return ""
    try:
        decoded_bytes, charset = decode_header(raw)[0]
        if isinstance(decoded_bytes, bytes):
            return decoded_bytes.decode(charset or "utf-8", errors="replace")
        return decoded_bytes or ""
    except Exception:
        return str(raw)


def _get_body(msg: email.message.Message) -> str:
    """
    Extract plain text from an email message.

    Emails can be multipart (HTML version + text version together).
    We only want text/plain — HTML bloats token count for the LLM.
    We cap at 2000 chars — plenty for classification, keeps LLM cost low.
    """
    limit = 2000

    if msg.is_multipart():
        # Walk every MIME part and grab the first text/plain
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")[:limit]
    else:
        # Single-part email — decode directly
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")[:limit]

    return ""   # empty body — still processable


# ── Main fetch function ───────────────────────────────────────────

def fetch_unseen_emails() -> list[dict]:
    """
    Connect to Gmail via IMAP SSL, search for UNSEEN emails,
    parse each one, return as a list of dicts.

    Each dict has these keys:
        message_id → unique RFC ID (used as dedup key in DB)
        sender     → "Name <email@domain.com>"
        subject    → decoded subject line
        body       → plain text (max 2000 chars)
        raw_uid    → IMAP session UID (not stored, just for reference)

    Returns [] on any connection error.
    The polling loop in main.py continues even if this returns empty.
    """
    results = []

    if not settings.EMAIL_ADDRESS or not settings.EMAIL_PASSWORD:
        logger.error("Email credentials missing — check .env")
        return results

    try:
        logger.info("Connecting to %s:%s", settings.IMAP_SERVER, settings.IMAP_PORT)

        # Open SSL-encrypted connection to Gmail
        mail = imaplib.IMAP4_SSL(settings.IMAP_SERVER, settings.IMAP_PORT)
        mail.login(settings.EMAIL_ADDRESS, settings.EMAIL_PASSWORD)
        mail.select("INBOX")   # switch to the INBOX folder

        # Ask the server for all emails we haven't read yet
        status, uid_list = mail.search(None, "UNSEEN")
        if status != "OK":
            logger.warning("IMAP search returned: %s", status)
            return results

        # uid_list[0] is a space-separated byte string: b"1 2 3"
        uids = uid_list[0].split()
        uids = uids[-1:] 
        logger.info("Found %d unseen email(s)", len(uids))

        for uid in uids:
            try:
                # RFC822 = fetch the complete raw email
                _, msg_data = mail.fetch(uid, "(RFC822)")
                raw_bytes = msg_data[0][1]
                msg = email.message_from_bytes(raw_bytes)

                results.append({
                    "raw_uid":    uid.decode(),
                    "message_id": msg.get("Message-ID", uid.decode()).strip(),
                    "sender":     _decode_str(msg.get("From")),
                    "subject":    _decode_str(msg.get("Subject")),
                    "body":       _get_body(msg),
                })
            except Exception as e:
                # One bad email shouldn't stop the rest
                logger.error("Failed to parse uid=%s: %s", uid, e)

        mail.logout()

    except imaplib.IMAP4.error as e:
        logger.error("IMAP auth error (check App Password): %s", e)
    except Exception as e:
        logger.error("Fetcher error: %s", e, exc_info=True)

    return results


# ── Dummy emails (no Gmail needed) ────────────────────────────────

def get_dummy_emails() -> list[dict]:
    """
    Five hardcoded test emails covering all 5 categories.

    USE THIS to run and test the full pipeline on day one
    without setting up Gmail at all.

    HOW TO USE:
        python main.py --dummy --once

    EACH EMAIL TESTS A DIFFERENT CATEGORY:
        dummy-001 → Action   (urgent, must do something)
        dummy-002 → Sales    (vendor pitch)
        dummy-003 → Support  (user needs help)
        dummy-004 → Spam     (obvious scam)
        dummy-005 → Important (personal message)
    """
    return [
        {
            "message_id": "<dummy-001@test.com>",
            "sender":     "boss@mycompany.com",
            "subject":    "URGENT: Please sign the contract by EOD",
            "body":       (
                "Hi, the client is waiting. I need you to review and sign "
                "the attached contract before 5pm today. This is blocking "
                "the entire project. Please action this immediately."
            ),
            "raw_uid": "1",
        },
        {
            "message_id": "<dummy-002@test.com>",
            "sender":     "sales@softwarevendor.com",
            "subject":    "Partnership opportunity — let's connect",
            "body":       (
                "Hello, I hope this finds you well. We offer an amazing SaaS "
                "platform that can 10x your team's productivity. I'd love to "
                "schedule a 15-minute demo. Would Thursday work for you?"
            ),
            "raw_uid": "2",
        },
        {
            "message_id": "<dummy-003@test.com>",
            "sender":     "user123@gmail.com",
            "subject":    "I cannot log into my account",
            "body":       (
                "Hi support team, I have been trying to reset my password "
                "for the past two days but the reset link keeps expiring "
                "before I can use it. I am locked out of my account completely. "
                "Please help."
            ),
            "raw_uid": "3",
        },
        {
            "message_id": "<dummy-004@test.com>",
            "sender":     "noreply@prizeclaim.net",
            "subject":    "YOU WON!! Claim your FREE iPhone NOW!!!",
            "body":       (
                "CONGRATULATIONS! You have been selected as our lucky winner. "
                "Click here immediately to claim your FREE iPhone 15 Pro. "
                "This offer expires in 24 hours. Act now!"
            ),
            "raw_uid": "4",
        },
        {
            "message_id": "<dummy-005@test.com>",
            "sender":     "alice@friend.com",
            "subject":    "Catching up — coffee this week?",
            "body":       (
                "Hey! It has been a while since we last caught up. "
                "Are you free for coffee sometime this week? "
                "Would love to hear what you have been up to."
            ),
            "raw_uid": "5",
        },
    ]
