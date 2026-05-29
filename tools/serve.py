#!/usr/bin/env python3
"""
doit — Multi-user task manager web server
"""

import functools
import json
import os
import re
import secrets
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    from flask import (Flask, abort, g, redirect, render_template,
                       request, session, url_for)
except ImportError:
    sys.exit("Error: flask not installed. Run: pip install flask")

sys.path.insert(0, str(Path(__file__).parent))

from config import cfg_get, cfg_bool, cfg_int
from task_manager import (read_tasks, write_tasks, get_all_contexts,
                           get_all_projects, get_tasks_file)
from user_store import UserStore
from mailer import send_verification_email, send_reset_email

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

app = Flask(__name__, template_folder="templates")

# Secret key
_secret_file = DATA_DIR / ".secret"
_secret = cfg_get("server", "secret")
if _secret:
    app.secret_key = _secret
elif _secret_file.exists():
    app.secret_key = _secret_file.read_text().strip()
else:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _key = os.urandom(24).hex()
    _secret_file.write_text(_key)
    app.secret_key = _key

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=cfg_bool("server", "https"),
)

# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def generate_csrf() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf(token: str) -> bool:
    return token and token == session.get("csrf_token")


@app.before_request
def _enforce_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    # Skip JSON API endpoints — they rely on same-origin via Content-Type
    if request.is_json:
        return
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not validate_csrf(token):
        abort(403)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_rate_buckets: dict = defaultdict(list)


def _check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Return True if allowed, False if rate limited."""
    now = time.time()
    bucket = _rate_buckets[key]
    # Remove expired
    _rate_buckets[key] = [t for t in bucket if now - t < window_seconds]
    if len(_rate_buckets[key]) >= max_requests:
        return False
    _rate_buckets[key].append(now)
    return True


def _ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ---------------------------------------------------------------------------
# User loading
# ---------------------------------------------------------------------------

@app.before_request
def load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        user = UserStore.get_user(user_id)
        if user and not user.get("suspended"):
            g.user = user
        else:
            session.clear()


@app.context_processor
def inject_globals():
    path = request.path
    if path.startswith("/tasks"):
        active = "tasks"
    elif path.startswith("/settings"):
        active = "settings"
    elif path.startswith("/admin"):
        active = "admin"
    else:
        active = None
    return {
        "current_user": g.user,
        "csrf_token": generate_csrf(),
        "active": active,
    }


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def require_login(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not g.user:
            return redirect(url_for("auth_login", next=request.full_path.rstrip("?")))
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not g.user:
            return redirect(url_for("auth_login"))
        if not g.user.get("admin"):
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_next(url: str) -> str:
    """Reject absolute URLs to prevent open redirect."""
    if not url:
        return url_for("tasks")
    if url.startswith(("http://", "https://", "//")):
        return url_for("tasks")
    return url


def _base_url() -> str:
    return request.host_url.rstrip("/")


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("tasks"))


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("tasks"))

    error = None
    if request.method == "POST":
        if not _check_rate_limit(f"register:{_ip()}", 5, 3600):
            error = "Too many registration attempts. Please try again later."
        else:
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")

            if not email or "@" not in email:
                error = "A valid email address is required."
            elif not password:
                error = "Password is required."
            elif len(password) < 8:
                error = "Password must be at least 8 characters."
            elif password != confirm:
                error = "Passwords do not match."
            else:
                try:
                    profile = UserStore.create_user(email, password)
                    token = UserStore.create_verify_token(profile["id"])
                    send_verification_email(email, token, _base_url())
                    return redirect(url_for("auth_verify_pending"))
                except ValueError as e:
                    error = str(e)

    return render_template("register.html", error=error)


@app.route("/auth/verify/pending")
def auth_verify_pending():
    return render_template("verify_pending.html")


@app.route("/auth/verify/<token>")
def auth_verify(token):
    user = UserStore.consume_verify_token(token)
    if not user:
        return render_template("verify_pending.html",
                               error="Verification link is invalid or expired.")
    session["user_id"] = user["id"]
    return redirect(url_for("tasks"))


@app.route("/auth/resend-verify", methods=["POST"])
def auth_resend_verify():
    email = request.form.get("email", "").strip().lower()
    user = UserStore.get_by_email(email) if email else None
    if user and not user.get("verified"):
        token = UserStore.create_verify_token(user["id"])
        send_verification_email(email, token, _base_url())
    return redirect(url_for("auth_verify_pending"))


@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    if g.user:
        return redirect(url_for("tasks"))

    error = None
    if request.method == "POST":
        if not _check_rate_limit(f"login:{_ip()}", 10, 300):
            error = "Too many login attempts. Please try again in a few minutes."
        else:
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = UserStore.authenticate(email, password)
            if user:
                if not user.get("verified"):
                    # Resend verification
                    token = UserStore.create_verify_token(user["id"])
                    send_verification_email(user["email"], token, _base_url())
                    return redirect(url_for("auth_verify_pending"))
                if user.get("suspended"):
                    error = "This account has been suspended."
                else:
                    session["user_id"] = user["id"]
                    next_url = _safe_next(
                        request.form.get("next") or request.args.get("next"))
                    return redirect(next_url)
            else:
                error = "Incorrect email or password."

    return render_template("login.html", error=error,
                           next=request.args.get("next", ""))


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))


@app.route("/auth/forgot", methods=["GET", "POST"])
def auth_forgot():
    success = False
    if request.method == "POST":
        if not _check_rate_limit(f"forgot:{_ip()}", 3, 3600):
            pass  # still show success — no enumeration
        else:
            email = request.form.get("email", "").strip().lower()
            user = UserStore.get_by_email(email) if email else None
            if user:
                token = UserStore.create_reset_token(user["id"])
                send_reset_email(email, token, _base_url())
        success = True

    return render_template("forgot_password.html", success=success)


@app.route("/auth/reset/<token>", methods=["GET", "POST"])
def auth_reset(token):
    user = UserStore.consume_reset_token(token)
    if not user:
        return render_template("reset_password.html",
                               error="Reset link is invalid or expired.", token=token)

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not password or len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            UserStore.change_password(user["id"], password)
            return redirect(url_for("auth_login"))

    return render_template("reset_password.html", error=error, token=token)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
@require_login
def settings():
    error = None
    success = None

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "prefs":
            prefs = UserStore.get_prefs(g.user["id"])
            prefs["theme"] = request.form.get("theme", prefs["theme"])
            prefs["font_size"] = request.form.get("font_size", prefs["font_size"])
            prefs["sort_col"] = request.form.get("sort_col", prefs["sort_col"])
            prefs["sort_dir"] = request.form.get("sort_dir", prefs["sort_dir"])
            UserStore.save_prefs(g.user["id"], prefs)
            success = "Preferences saved."

        elif action == "change-email":
            new_email = request.form.get("email", "").strip().lower()
            if not new_email or "@" not in new_email:
                error = "A valid email address is required."
            else:
                try:
                    UserStore.change_email(g.user["id"], new_email)
                    token = UserStore.create_verify_token(g.user["id"])
                    send_verification_email(new_email, token, _base_url())
                    session.clear()
                    return redirect(url_for("auth_verify_pending"))
                except ValueError as e:
                    error = str(e)

        elif action == "change-password":
            current = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            user_check = UserStore.authenticate(g.user["email"], current)
            if not user_check:
                error = "Current password is incorrect."
            elif len(new_pw) < 8:
                error = "New password must be at least 8 characters."
            elif new_pw != confirm:
                error = "Passwords do not match."
            else:
                UserStore.change_password(g.user["id"], new_pw)
                success = "Password changed."

    prefs = UserStore.get_prefs(g.user["id"])
    return render_template("settings.html", prefs=prefs, error=error, success=success)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.route("/admin")
@require_admin
def admin():
    return redirect(url_for("admin_users"))


@app.route("/admin/users")
@require_admin
def admin_users():
    users = UserStore.list_users()
    for u in users:
        u["_task_count"] = UserStore.get_task_count(u["id"])
    return render_template("admin.html", users=users)


@app.route("/admin/users/<user_id>/suspend", methods=["POST"])
@require_admin
def admin_suspend(user_id):
    user = UserStore.get_user(user_id)
    if user:
        UserStore.suspend_user(user_id, not user.get("suspended"))
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/delete", methods=["POST"])
@require_admin
def admin_delete(user_id):
    if user_id == g.user["id"]:
        abort(400)
    if request.args.get("confirm") != "yes":
        abort(400)
    UserStore.delete_user(user_id)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/toggle-admin", methods=["POST"])
@require_admin
def admin_toggle_admin(user_id):
    if user_id == g.user["id"]:
        abort(400)
    UserStore.toggle_admin(user_id)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/resend-verify", methods=["POST"])
@require_admin
def admin_resend_verify(user_id):
    user = UserStore.get_user(user_id)
    if user and not user.get("verified"):
        token = UserStore.create_verify_token(user_id)
        send_verification_email(user["email"], token, _base_url())
    return redirect(url_for("admin_users"))


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def _get_tasks_file():
    return get_tasks_file(g.user["id"])


def _toggle_task(line_num: int, action: str) -> bool:
    tasks_file = _get_tasks_file()
    if not tasks_file.exists():
        return False
    lines = tasks_file.read_text(encoding="utf-8").splitlines()

    task_count = 0
    for i, line in enumerate(lines):
        if re.match(r"^\s*- \[[x ]\]", line):
            if task_count == line_num:
                if action == "complete":
                    lines[i] = re.sub(r"\[ \]", "[x]", lines[i], count=1)
                else:
                    lines[i] = re.sub(r"\[x\]", "[ ]", lines[i], count=1)
                tasks_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
            task_count += 1
    return False


def _add_task(text: str, section: str = "Inbox") -> None:
    tasks_file = _get_tasks_file()
    if not tasks_file.exists():
        tasks_file.parent.mkdir(parents=True, exist_ok=True)
        tasks_file.write_text("# Tasks\n\n## Inbox\n\n", encoding="utf-8")
    content = tasks_file.read_text(encoding="utf-8")
    section_header = f"## {section}"
    if section_header in content:
        insert_pos = content.index(section_header) + len(section_header)
        content = content[:insert_pos] + f"\n- [ ] {text}" + content[insert_pos:]
    else:
        content = content.rstrip() + f"\n\n{section_header}\n\n- [ ] {text}\n"
    tasks_file.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Task routes
# ---------------------------------------------------------------------------

@app.route("/tasks")
@require_login
def tasks():
    tasks_file = _get_tasks_file()
    tasks_list = read_tasks(tasks_file)
    tasks_list.sort(key=lambda t: t.due or "9999-12-31")
    return render_template("tasks_view.html", tasks=tasks_list,
                           all_contexts=get_all_contexts(tasks_file),
                           all_projects=get_all_projects(tasks_file))


@app.route("/tasks/toggle", methods=["POST"])
@require_login
def tasks_toggle():
    data = request.get_json(silent=True) or {}
    line = data.get("line")
    action = data.get("action")
    if line is None or action not in ("complete", "reopen"):
        return {"error": "bad request"}, 400
    with UserStore.get_user_lock(g.user["id"]):
        ok = _toggle_task(int(line), action)
    return {"ok": ok}


@app.route("/tasks/add", methods=["POST"])
@require_login
def tasks_add():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    section = (data.get("section") or "Inbox").strip()
    if not text:
        return {"error": "Empty task"}, 400
    with UserStore.get_user_lock(g.user["id"]):
        _add_task(text, section)
    return {"ok": True}


@app.route("/tasks/update", methods=["POST"])
@require_login
def tasks_update():
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    field = data.get("field")
    value = data.get("value", "").strip()

    if task_id is None or field is None:
        return {"error": "missing task_id or field"}, 400

    tasks_file = _get_tasks_file()
    with UserStore.get_user_lock(g.user["id"]):
        tasks_list = read_tasks(tasks_file)
        if not (0 <= task_id < len(tasks_list)):
            return {"error": "task not found"}, 404

        task = tasks_list[task_id]
        next_task = None

        if field == "description":
            task.description = value
        elif field == "context":
            task.set_context(value if value else None)
        elif field == "due":
            task.set_due(value if value else None)
        elif field == "priority":
            task.set_priority(value if value else None)
        elif field == "project":
            task.set_project(value if value else None)
        elif field == "recurrence":
            task.set_recurrence(value if value else None)
        elif field == "start":
            task.set_start(value if value else None)
        elif field == "notes":
            task.set_notes(value)
        elif field == "complete":
            if value == "true":
                task.complete_task()
                next_task = task.get_next_recurrence()
            else:
                task.reopen_task()
        else:
            return {"error": "unknown field"}, 400

        write_tasks(tasks_list, tasks_file)

        if next_task:
            next_line = next_task.to_line()
            next_notes = next_task.raw_notes.strip()
            with open(tasks_file, "a", encoding="utf-8") as f:
                f.write("\n" + next_line)
                if next_notes:
                    for note_line in next_notes.split("\n"):
                        f.write("\n" + note_line)

    result = {"ok": True}
    if next_task:
        result["next_task"] = {
            "description": next_task.description,
            "due": next_task.due,
            "priority": next_task.priority,
            "context": next_task.context,
            "recurrence": next_task.recurrence,
        }
    return result


@app.route("/tasks/bulk-update", methods=["POST"])
@require_login
def tasks_bulk_update():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    task_ids = data.get("task_ids", [])
    value = data.get("value", "").strip()

    if action is None:
        return {"error": "missing action"}, 400

    tasks_file = _get_tasks_file()
    with UserStore.get_user_lock(g.user["id"]):
        tasks_list = read_tasks(tasks_file)

        for task_id in task_ids:
            if not (0 <= task_id < len(tasks_list)):
                continue
            task = tasks_list[task_id]
            if action == "set-priority":
                task.set_priority(value if value else None)
            elif action == "set-context":
                task.set_context(value if value else None)
            elif action == "set-due":
                task.set_due(value if value else None)
            elif action == "set-project":
                task.set_project(value if value else None)
            elif action == "delete":
                task.description = "[DELETED]"
                task.complete = True
            else:
                return {"error": "unknown action"}, 400

        write_tasks(tasks_list, tasks_file)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = cfg_get("server", "host", "127.0.0.1")
    port = cfg_int("server", "port", 8080)
    debug = cfg_bool("server", "debug", False)
    print(f"Starting doit at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
