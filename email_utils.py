import base64
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Union

# Load .env file to ensure config is available
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # dotenv not installed, rely on environment

from utils.circuit_breaker import get_breaker


def _parse_recipients(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    parts = [p.strip() for p in str(value).replace(";", ",").split(",")]
    return [p for p in parts if p]


def _send_via_gmail_api(msg: EmailMessage, from_addr: str) -> bool:
    """Send email using Gmail API with gcloud OAuth credentials."""
    try:
        from google.auth import default
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        # Get credentials with Gmail send scope
        creds, _ = default(scopes=['https://www.googleapis.com/auth/gmail.send'])
        creds.refresh(Request())

        service = build('gmail', 'v1', credentials=creds)

        # Encode the message
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')

        # Send it
        service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        return True
    except Exception as e:
        print(f"Gmail API error: {e}")
        return False


def _send_via_smtp(msg: EmailMessage, host: str, port: int, user: str, password: str) -> bool:
    """Send email using traditional SMTP."""
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"SMTP error: {e}")
        return False


def send_email(
    *,
    subject: str,
    body: str,
    to_addrs: Union[Iterable[str], str] = None,
    to_email: str = None,  # Alias for single recipient (used by review_notifier)
    html: bool = False
) -> bool:
    """
    Send an email via Gmail API (preferred) or SMTP fallback.

    Args:
        subject: Email subject
        body: Email body (plain text or HTML depending on `html` flag)
        to_addrs: Recipient(s) - can be string or list
        to_email: Single recipient (alias for to_addrs, for compatibility)
        html: If True, send body as HTML email

    Environment variables:
        OMEGA_EMAIL_METHOD: "gmail_api" (default) or "smtp"
        OMEGA_SMTP_HOST: SMTP server (default: smtp.gmail.com)
        OMEGA_SMTP_PORT: SMTP port (default: 587)
        OMEGA_SMTP_USER: SMTP username / from address
        OMEGA_SMTP_PASS: SMTP password (App Password for Gmail)
        OMEGA_SMTP_FROM: From address (defaults to SMTP_USER)
    """
    user = os.environ.get("OMEGA_SMTP_USER", "").strip()
    from_addr = os.environ.get("OMEGA_SMTP_FROM", user).strip()
    method = os.environ.get("OMEGA_EMAIL_METHOD", "gmail_api").strip().lower()

    # Support both to_addrs and to_email parameters
    recipients = _parse_recipients(to_addrs or to_email)
    if not (from_addr and recipients):
        print(f"Email config missing: from_addr={from_addr}, recipients={recipients}")
        return False

    breaker = get_breaker("email")
    if breaker.is_open():
        return False

    # Build the message
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    if html:
        msg.set_content("Please view this email in an HTML-capable client.")
        msg.add_alternative(body or "", subtype="html")
    else:
        msg.set_content(body or "")

    success = False

    # Try Gmail API first (if configured)
    if method == "gmail_api":
        success = _send_via_gmail_api(msg, from_addr)
        if not success:
            print("Gmail API failed, trying SMTP fallback...")
            method = "smtp"  # Fall through to SMTP

    # SMTP fallback or primary
    if method == "smtp" and not success:
        host = os.environ.get("OMEGA_SMTP_HOST", "smtp.gmail.com").strip()
        port = int(os.environ.get("OMEGA_SMTP_PORT", "587"))
        password = os.environ.get("OMEGA_SMTP_PASS", "").strip()

        if not (host and port and user and password):
            print(f"SMTP config incomplete: host={host}, port={port}, user={bool(user)}, pass={bool(password)}")
            breaker.record_failure()
            return False

        success = _send_via_smtp(msg, host, port, user, password)

    if success:
        breaker.record_success()
    else:
        breaker.record_failure()

    return success
