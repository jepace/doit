#!/usr/bin/env python3
"""
Daily digest emailer — send each opted-in user their today+overdue task list.

Run via cron at whatever time you want the email delivered, e.g.:
  0 7 * * * cd /var/www/doit && python3 src/daily_digest.py

Or pass --user <email> to send a one-off to a specific user (useful for testing).
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import cfg_get
from task_manager import read_tasks, sort_tasks, get_tasks_file
from user_store import UserStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def build_email(user: dict, tasks: list, today: str) -> tuple[str, str]:
    """Return (subject, html_body) for the digest."""
    subject = f"Do It! — {today}"

    if not tasks:
        html = f"""<html><body style="font-family:-apple-system,sans-serif;color:#1c1c1e;padding:20px;">
<h2 style="font-size:18px;margin:0 0 4px;">Do It! <span style="font-weight:400;">({today})</span></h2>
<p style="color:#8e8e93;font-size:14px;">Nothing due today or overdue — you're clear!</p>
</body></html>"""
        return subject, html

    rows = ""
    for t in tasks:
        overdue = t.due and t.due < today
        name_style = "font-size:13px;font-weight:500;" + ("color:#c00;" if overdue else "")
        ctx = f'<td style="font-size:11px;color:#555;padding:7px 0 7px 12px;white-space:nowrap;">{t.context or ""}</td>'
        rows += f"""<tr>
  <td style="padding:7px 8px 7px 0;vertical-align:middle;">
    <div style="width:14px;height:14px;border:1.5px solid #333;border-radius:2px;"></div>
  </td>
  <td style="padding:7px 0;border-bottom:1px solid #d8d8d8;width:100%;">
    <span style="{name_style}">{t.description}</span>
    {"<br><span style='font-size:10px;color:#888;'>due " + t.due + "</span>" if overdue else ""}
  </td>
  {ctx}
</tr>"""

    base_url = cfg_get("server", "base_url", "").rstrip("/")
    html = f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;color:#1c1c1e;padding:24px;max-width:520px;">
<h2 style="font-size:18px;margin:0 0 16px;font-weight:700;">Do It! <span style="font-weight:400;">({today})</span></h2>
<table style="width:100%;border-collapse:collapse;">
{rows}
</table>
{"<p style='margin-top:16px;'><a href='" + base_url + "' style='color:#007aff;font-size:13px;'>Open Do It!</a></p>" if base_url else ""}
</body></html>"""
    return subject, html


def send_digest(user: dict, today: str) -> bool:
    from mailer import send_email
    tasks_file = get_tasks_file(user["id"])
    if not tasks_file.exists():
        return False
    all_tasks = read_tasks(tasks_file)
    tasks = [t for t in all_tasks
             if t.section != "Archive" and not t.complete
             and t.due and t.due <= today]
    tasks = sort_tasks(tasks, user.get("prefs") or {})
    subject, html = build_email(user, tasks, today)
    text = f"Do It! — {today}\n\n" + "\n".join(
        f"[ ] {t.description}" + (f"  [{t.context}]" if t.context else "")
        for t in tasks
    ) or "Nothing due today or overdue."
    ok = send_email(user["email"], subject, text, html)
    log.info("%s digest → %s: %s", today, user["email"], "sent" if ok else "FAILED")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Send daily digest emails.")
    parser.add_argument("--user", help="Send only to this email address (for testing)")
    args = parser.parse_args()

    today = date.today().isoformat()
    users = UserStore.list_users()

    if args.user:
        users = [u for u in users if u["email"] == args.user]
        if not users:
            log.error("User not found: %s", args.user)
            sys.exit(1)

    sent = failed = skipped = 0
    for user in users:
        if not args.user and not user.get("prefs", {}).get("daily_digest"):
            skipped += 1
            continue
        if not user.get("verified"):
            skipped += 1
            continue
        if send_digest(user, today):
            sent += 1
        else:
            failed += 1

    log.info("Done — sent: %d, failed: %d, skipped: %d", sent, failed, skipped)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
