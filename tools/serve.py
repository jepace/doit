#!/usr/bin/env python3
"""
doit — Task manager web server (multi-user)

Usage:
  cp config.json.example config.json   # fill in settings
  python3 tools/serve.py               # http://127.0.0.1:8080
"""

import functools
import os
import re
import secrets
import sys
import time
from pathlib import Path

try:
    from flask import (Flask, g, abort, flash, redirect, render_template,
                       request, session, url_for)
except ImportError:
    sys.exit("Error: flask not installed. Run: pip install flask")

sys.path.insert(0, str(Path(__file__).parent))

from config import cfg_get, cfg_bool, cfg_int
from task_manager import (read_tasks, write_tasks, get_all_contexts,
                          get_all_projects, get_tasks_file, DATA_DIR)
from user_store import UserStore
from mailer import send_verification_email, send_reset_email

REPO_ROOT = Path(__file__).resolve().parent.parent

app = Flask(__name__, template_folder="templates")

_secret_file = DATA_DIR / ".secret"
_secret = cfg_get("server", "secret")
if _secret:
    app.secret_key = _secret
elif _secret_file.exists():
    app.secret_key = _secret_file.read_text().strip()
else:
    _key = os.urandom(24).hex()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _secret_file.write_text(_key)
    app.secret_key = _key

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=cfg_bool("server", "https"),
)


# ---------------------------------------------------------------------------
# CSRF helpers
# ---------------------------------------------------------------------------

def generate_csrf() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf() -> bool:
    form_token = request.form.get("_csrf_token", "")
    session_token = session.get("csrf_token", "")
    if not form_token or not session_token:
        return False
    return secrets.compare_digest(form_token, session_token)


@app.before_request
def csrf_protect():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    # Skip CSRF check for JSON API endpoints (they use session auth + same-origin)
    if request.is_json:
        return
    if not validate_csrf():
        abort(403)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_rate_limit: dict[str, list[float]] = {}


def _check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Returns True if request is allowed, False if over limit."""
    now = time.time()
    timestamps = _rate_limit.get(key, [])
    # Prune old timestamps
    timestamps = [t for t in timestamps if now - t < window_seconds]
    if len(timestamps) >= max_requests:
        _rate_limit[key] = timestamps
        return False
    timestamps.append(now)
    _rate_limit[key] = timestamps
    return True


def _ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _safe_next(url: str) -> str:
    """Return url only if it's a safe relative path, otherwise /tasks."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return url_for("tasks")
    return url or url_for("tasks")


# ---------------------------------------------------------------------------
# User context
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
            # User deleted or suspended — clear session
            session.clear()


@app.context_processor
def inject_globals():
    csrf_token = generate_csrf()
    path   = request.path
    active = ("settings" if path.startswith("/settings")
              else "admin"  if path.startswith("/admin")
              else "tasks")
    return {"current_user": g.get("user"), "csrf_token": csrf_token, "active": active}


# ---------------------------------------------------------------------------
# Auth decorators
# ---------------------------------------------------------------------------

def require_login(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not g.get("user"):
            return redirect(url_for("auth_login", next=request.full_path.rstrip("?")))
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not g.get("user"):
            return redirect(url_for("auth_login"))
        if not g.user.get("admin"):
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("tasks"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.get("user"):
        return redirect(url_for("tasks"))
    error = None
    if request.method == "POST":
        ip = _ip()
        if not _check_rate_limit(f"register:{ip}", 5, 3600):
            error = "Too many registration attempts. Please try again later."
        else:
            email    = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            confirm  = request.form.get("confirm", "")
            if not email:
                error = "Email is required."
            elif not password:
                error = "Password is required."
            elif len(password) < 8:
                error = "Password must be at least 8 characters."
            elif password != confirm:
                error = "Passwords do not match."
            else:
                try:
                    user = UserStore.create_user(email, password)
                    token = UserStore.create_verify_token(user["id"])
                    base_url = cfg_get("server", "base_url", f"http://127.0.0.1:{cfg_int('server', 'port', 8080)}")
                    send_verification_email(email, token, base_url)
                    session["pending_email"] = email
                    return redirect(url_for("verify_pending"))
                except ValueError as exc:
                    error = str(exc)
    return render_template("register.html", error=error)


@app.route("/auth/verify/pending")
def verify_pending():
    email = session.get("pending_email") or request.args.get("email", "")
    return render_template("verify_pending.html", email=email)


@app.route("/auth/verify/<token>")
def auth_verify(token):
    user = UserStore.consume_verify_token(token)
    if user is None:
        return render_template("verify_pending.html", email="",
                               error="This verification link is invalid or has expired.")
    session.pop("pending_email", None)
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_hex(32)
    flash("Your email has been verified. Welcome!")
    return redirect(url_for("tasks"))


@app.route("/auth/resend-verify", methods=["POST"])
def auth_resend_verify():
    email = request.form.get("email", "").strip() or session.get("pending_email", "")
    if email:
        user = UserStore.get_by_email(email)
        if user and not user.get("verified"):
            token = UserStore.create_verify_token(user["id"])
            base_url = cfg_get("server", "base_url", f"http://127.0.0.1:{cfg_int('server', 'port', 8080)}")
            send_verification_email(email, token, base_url)
    session["pending_email"] = email
    return redirect(url_for("verify_pending"))


@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    if g.get("user"):
        return redirect(url_for("tasks"))
    error = None
    if request.method == "POST":
        ip = _ip()
        if not _check_rate_limit(f"login:{ip}", 10, 300):
            error = "Too many login attempts. Please try again later."
        else:
            email    = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            user, err = UserStore.authenticate(email, password)
            if user:
                session.clear()
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_hex(32)
                next_url = _safe_next(request.form.get("next") or request.args.get("next", ""))
                return redirect(next_url)
            elif err == "Please verify your email address before signing in.":
                # Resend verification and show pending page
                user_obj = UserStore.get_by_email(email)
                if user_obj:
                    token = UserStore.create_verify_token(user_obj["id"])
                    base_url = cfg_get("server", "base_url", f"http://127.0.0.1:{cfg_int('server', 'port', 8080)}")
                    send_verification_email(email, token, base_url)
                    session["pending_email"] = email
                    return redirect(url_for("verify_pending"))
            error = err
    return render_template("login.html", error=error,
                           next=request.args.get("next", ""))


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))


@app.route("/auth/forgot", methods=["GET", "POST"])
def auth_forgot():
    submitted = False
    if request.method == "POST":
        ip = _ip()
        if _check_rate_limit(f"forgot:{ip}", 3, 3600):
            email = request.form.get("email", "").strip()
            if email:
                token, user = UserStore.create_reset_token(email)
                if token and user:
                    base_url = cfg_get("server", "base_url", f"http://127.0.0.1:{cfg_int('server', 'port', 8080)}")
                    send_reset_email(email, token, base_url)
        # Always show success to avoid enumeration
        submitted = True
    return render_template("forgot_password.html", submitted=submitted)


@app.route("/auth/reset/<token>", methods=["GET", "POST"])
def auth_reset(token):
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if not password:
            error = "Password is required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            ok = UserStore.consume_reset_token(token, password)
            if ok:
                flash("Password reset successfully. Please sign in.")
                return redirect(url_for("auth_login"))
            error = "This reset link is invalid or has expired."
    return render_template("reset_password.html", token=token, error=error)


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
    data   = request.get_json(silent=True) or {}
    line   = data.get("line")
    action = data.get("action")
    if line is None or action not in ("complete", "reopen"):
        return {"error": "bad request"}, 400
    return {"ok": _toggle_task(int(line), action)}


@app.route("/tasks/add", methods=["POST"])
@require_login
def tasks_add():
    data    = request.get_json(silent=True) or {}
    text    = (data.get("text")    or "").strip()
    section = (data.get("section") or "Inbox").strip()
    if not text:
        return {"error": "Empty task"}, 400
    _add_task(text, section)
    return {"ok": True}


@app.route("/tasks/update", methods=["POST"])
@require_login
def tasks_update():
    data    = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    field   = data.get("field")
    value   = data.get("value", "").strip()

    if task_id is None or field is None:
        return {"error": "missing task_id or field"}, 400

    tasks_file = _get_tasks_file()
    tasks_list = read_tasks(tasks_file)
    if not (0 <= task_id < len(tasks_list)):
        return {"error": "task not found"}, 404

    task      = tasks_list[task_id]
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
        next_line  = next_task.to_line()
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
            "due":         next_task.due,
            "priority":    next_task.priority,
            "context":     next_task.context,
            "recurrence":  next_task.recurrence,
        }
    return result


@app.route("/tasks/bulk-update", methods=["POST"])
@require_login
def tasks_bulk_update():
    data     = request.get_json(silent=True) or {}
    action   = data.get("action")
    task_ids = data.get("task_ids", [])
    value    = data.get("value", "").strip()

    if action is None:
        return {"error": "missing action"}, 400

    tasks_file = _get_tasks_file()
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
# Admin routes
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
@require_login
def settings():
    user   = g.user
    errors = {}
    success = request.args.get("saved") == "1"

    if request.method == "POST":
        action = request.form.get("action")

        if action == "prefs":
            UserStore.save_prefs(
                user["id"],
                theme     = request.form.get("theme",    "system"),
                font_size = request.form.get("font_size","medium"),
                sort_col  = request.form.get("sort_col", "due"),
                sort_dir  = request.form.get("sort_dir", "asc"),
            )
            return redirect(url_for("settings", saved="1"))

        elif action == "email":
            new_email = request.form.get("new_email", "").strip().lower()
            try:
                UserStore.change_email(user["id"], new_email)
                token    = UserStore.create_verify_token(user["id"])
                base_url = cfg_get("server", "base_url",
                                   f"http://127.0.0.1:{cfg_int('server','port',8080)}")
                send_verification_email(new_email, token, base_url)
                session.clear()
                return redirect(url_for("verify_pending", email=new_email))
            except ValueError as e:
                errors["email"] = str(e)

        elif action == "password":
            current_pw = request.form.get("current_password", "")
            new_pw     = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")
            if new_pw != confirm_pw:
                errors["password"] = "New passwords do not match."
            else:
                ok, msg = UserStore.change_password(user["id"], current_pw, new_pw)
                if ok:
                    return redirect(url_for("settings", saved="1"))
                else:
                    errors["password"] = msg

    prefs = UserStore.get_prefs(user["id"])
    return render_template("settings.html", user=user, prefs=prefs,
                           errors=errors, success=success)


@app.route("/admin")
@require_admin
def admin_index():
    return redirect(url_for("admin_users"))


@app.route("/admin/users")
@require_admin
def admin_users():
    users = UserStore.list_users()
    # Add task count to each user dict for display
    for u in users:
        u["_task_count"] = UserStore.get_task_count(u["id"])
    users.sort(key=lambda u: u.get("created_at", ""))
    return render_template("admin.html", users=users)


@app.route("/admin/users/<user_id>/suspend", methods=["POST"])
@require_admin
def admin_suspend(user_id):
    user = UserStore.get_user(user_id)
    if user:
        UserStore.suspend_user(user_id, not user.get("suspended", False))
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/delete", methods=["POST"])
@require_admin
def admin_delete(user_id):
    if request.args.get("confirm") == "yes":
        # Cannot delete self
        if user_id != g.user["id"]:
            UserStore.delete_user(user_id)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/toggle-admin", methods=["POST"])
@require_admin
def admin_toggle_admin(user_id):
    # Cannot demote self
    if user_id == g.user["id"]:
        return redirect(url_for("admin_users"))
    UserStore.toggle_admin(user_id)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/resend-verify", methods=["POST"])
@require_admin
def admin_resend_verify(user_id):
    user = UserStore.get_user(user_id)
    if user and not user.get("verified"):
        token = UserStore.create_verify_token(user_id)
        base_url = cfg_get("server", "base_url", f"http://127.0.0.1:{cfg_int('server', 'port', 8080)}")
        send_verification_email(user["email"], token, base_url)
    return redirect(url_for("admin_users"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host  = cfg_get("server", "host", "127.0.0.1")
    port  = cfg_int("server", "port", 8080)
    debug = cfg_bool("server", "debug", False)

    # Ensure data dir exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not UserStore.list_users():
        print(f"\n  No users exist yet. Register at: http://{host}:{port}/register\n")

    print(f"Starting doit at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
