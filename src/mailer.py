#!/usr/bin/env python3
"""Email sending utilities for doit (Resend API only)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import cfg_get

logger = logging.getLogger(__name__)


def send_email(
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send an email via Resend. Returns True on success, False on failure."""
    resend_key = cfg_get("email", "resend_api_key", "").strip()
    if not resend_key:
        logger.warning("No resend_api_key configured. Email not sent.")
        return False

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
                "Authorization": f"Bearer {resend_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except Exception as exc:
        logger.error("Resend API error: %s", exc)
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
