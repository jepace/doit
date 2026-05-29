#!/usr/bin/env python3
"""
Email sending utilities for doit.
Tries Resend API first, falls back to SMTP.
"""

import logging
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import cfg_bool, cfg_get, cfg_int

logger = logging.getLogger(__name__)


def send_email(
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send an email. Returns True on success, False on failure."""
    resend_key = cfg_get("email", "resend_api_key", "").strip()
    if resend_key:
        return _send_via_resend(resend_key, to_addr, subject, text_body, html_body)

    smtp_host = cfg_get("email", "smtp_host", "").strip()
    if smtp_host:
        return _send_via_smtp(smtp_host, to_addr, subject, text_body, html_body)

    logger.warning("No email transport configured (no resend_api_key or smtp_host). Email not sent.")
    return False


def _send_via_resend(
    api_key: str,
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: str | None,
) -> bool:
    try:
        import urllib.request
        import json as _json

        from_addr = cfg_get("email", "from_address", "noreply@example.com")
        payload: dict = {
            "from": from_addr,
            "to": [to_addr],
            "subject": subject,
            "text": text_body,
        }
        if html_body:
            payload["html"] = html_body

        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except Exception as exc:
        logger.error("Resend API error: %s", exc)
        return False


def _send_via_smtp(
    smtp_host: str,
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: str | None,
) -> bool:
    try:
        smtp_port = cfg_int("email", "smtp_port", 587)
        smtp_user = cfg_get("email", "smtp_user", "")
        smtp_pass = cfg_get("email", "smtp_password", "")
        smtp_tls  = cfg_bool("email", "smtp_tls", True)
        from_addr = cfg_get("email", "from_address", "noreply@example.com")

        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
        else:
            msg = MIMEText(text_body, "plain")

        msg["Subject"] = subject
        msg["From"]    = from_addr
        msg["To"]      = to_addr

        if smtp_tls:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)

        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.sendmail(from_addr, [to_addr], msg.as_string())
        server.quit()
        return True
    except Exception as exc:
        logger.error("SMTP error: %s", exc)
        return False


def send_verification_email(email: str, token: str, base_url: str) -> bool:
    """Send an account verification email."""
    base_url = base_url.rstrip("/")
    verify_url = f"{base_url}/auth/verify/{token}"

    subject = "Verify your doit account"
    text_body = (
        f"Welcome to doit!\n\n"
        f"Please verify your email address by clicking the link below:\n\n"
        f"{verify_url}\n\n"
        f"This link expires in 24 hours.\n\n"
        f"If you did not create an account, you can ignore this email."
    )
    html_body = f"""<html><body>
<p>Welcome to doit!</p>
<p>Please verify your email address by clicking the link below:</p>
<p><a href="{verify_url}">{verify_url}</a></p>
<p>This link expires in 24 hours.</p>
<p>If you did not create an account, you can ignore this email.</p>
</body></html>"""
    return send_email(email, subject, text_body, html_body)


def send_reset_email(email: str, token: str, base_url: str) -> bool:
    """Send a password reset email."""
    base_url = base_url.rstrip("/")
    reset_url = f"{base_url}/auth/reset/{token}"

    subject = "Reset your doit password"
    text_body = (
        f"You requested a password reset for your doit account.\n\n"
        f"Click the link below to reset your password:\n\n"
        f"{reset_url}\n\n"
        f"This link expires in 1 hour.\n\n"
        f"If you did not request a password reset, you can ignore this email."
    )
    html_body = f"""<html><body>
<p>You requested a password reset for your doit account.</p>
<p>Click the link below to reset your password:</p>
<p><a href="{reset_url}">{reset_url}</a></p>
<p>This link expires in 1 hour.</p>
<p>If you did not request a password reset, you can ignore this email.</p>
</body></html>"""
    return send_email(email, subject, text_body, html_body)
