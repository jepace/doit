#!/usr/bin/env python3
"""
mailer.py — email sending for doit via Resend API.

Requires email.resend_api_key and email.from_address in config.json.
"""

import json
import logging
import sys
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg_get

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, text_body: str, html_body: str = None) -> bool:
    """Send an email via Resend. Returns True on success, False on failure."""
    api_key = cfg_get("email", "resend_api_key")
    if not api_key:
        logger.warning("email.resend_api_key not configured — email not sent to %s", to)
        return False

    from_addr = cfg_get("email", "from_address", "noreply@example.com")
    payload = {"from": from_addr, "to": [to], "subject": subject, "text": text_body}
    if html_body:
        payload["html"] = html_body

    req = urllib_request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except (URLError, Exception) as e:
        logger.error("Resend error sending to %s: %s", to, e)
        return False


def send_verification_email(email: str, token: str, base_url: str) -> bool:
    link = f"{base_url.rstrip('/')}/auth/verify/{token}"
    return send_email(
        email,
        "Verify your doit account",
        f"Click the link below to verify your email address:\n\n{link}\n\nThis link expires in 24 hours.",
        f'<p>Click to verify your email: <a href="{link}">{link}</a></p><p>Expires in 24 hours.</p>',
    )


def send_reset_email(email: str, token: str, base_url: str) -> bool:
    link = f"{base_url.rstrip('/')}/auth/reset/{token}"
    return send_email(
        email,
        "Reset your doit password",
        f"Click the link below to reset your password:\n\n{link}\n\nThis link expires in 1 hour.",
        f'<p>Click to reset your password: <a href="{link}">{link}</a></p><p>Expires in 1 hour.</p>',
    )
