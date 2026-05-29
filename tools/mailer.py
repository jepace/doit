#!/usr/bin/env python3
"""
mailer.py — email sending for doit.

Tries Resend API first (if email.resend_api_key configured),
falls back to SMTP (if email.smtp_host configured).
"""

import logging
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import URLError
import json

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg_get

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, text_body: str, html_body: str = None) -> bool:
    """Send an email. Returns True on success, False on failure."""
    resend_key = cfg_get("email", "resend_api_key")
    if resend_key:
        return _send_via_resend(resend_key, to, subject, text_body, html_body)

    smtp_host = cfg_get("email", "smtp_host")
    if smtp_host:
        return _send_via_smtp(smtp_host, to, subject, text_body, html_body)

    logger.warning("No email provider configured (email.resend_api_key or email.smtp_host). "
                   "Email not sent to %s: %s", to, subject)
    return False


def _send_via_resend(api_key: str, to: str, subject: str, text_body: str, html_body: str = None) -> bool:
    from_addr = cfg_get("email", "from_address", "noreply@example.com")
    payload = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body

    data = json.dumps(payload).encode()
    req = urllib_request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except (URLError, Exception) as e:
        logger.error("Resend API error sending to %s: %s", to, e)
        return False


def _send_via_smtp(smtp_host: str, to: str, subject: str, text_body: str, html_body: str = None) -> bool:
    smtp_port = int(cfg_get("email", "smtp_port", "587"))
    smtp_user = cfg_get("email", "smtp_user")
    smtp_pass = cfg_get("email", "smtp_pass")
    from_addr = cfg_get("email", "from_address", smtp_user or "noreply@example.com")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.ehlo()
            if smtp_port != 465:
                s.starttls()
                s.ehlo()
            if smtp_user and smtp_pass:
                s.login(smtp_user, smtp_pass)
            s.sendmail(from_addr, [to], msg.as_string())
        return True
    except Exception as e:
        logger.error("SMTP error sending to %s: %s", to, e)
        return False


def send_verification_email(email: str, token: str, base_url: str) -> bool:
    link = f"{base_url.rstrip('/')}/auth/verify/{token}"
    subject = "Verify your doit account"
    text_body = f"Click the link below to verify your email address:\n\n{link}\n\nThis link expires in 24 hours."
    html_body = (
        f"<p>Click the link below to verify your email address:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>This link expires in 24 hours.</p>"
    )
    return send_email(email, subject, text_body, html_body)


def send_reset_email(email: str, token: str, base_url: str) -> bool:
    link = f"{base_url.rstrip('/')}/auth/reset/{token}"
    subject = "Reset your doit password"
    text_body = f"Click the link below to reset your password:\n\n{link}\n\nThis link expires in 1 hour."
    html_body = (
        f"<p>Click the link below to reset your password:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>This link expires in 1 hour.</p>"
    )
    return send_email(email, subject, text_body, html_body)
