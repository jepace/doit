#!/usr/bin/env python3
"""
doit — Task manager web server (multi-user)

Usage:
  cp config.json.example config.json   # fill in settings
  python3 src/serve.py               # http://127.0.0.1:8080
"""

import functools
import logging
import logging.config
import os
import re
import secrets
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

try:
    from flask import (Flask, g, abort, flash, redirect, render_template,
                       request, session, url_for)
    from werkzeug.middleware.proxy_fix import ProxyFix
except ImportError:
    sys.exit("Error: flask not installed. Run: pip install flask")

sys.path.insert(0, str(Path(__file__).parent))

from config import cfg_get, cfg_bool, cfg_int
from task_manager import (read_tasks, write_tasks, get_all_contexts,
                          get_all_projects, get_tasks_file, DATA_DIR,
                          _write_text_atomic)
from user_store import UserStore, get_user_lock
from mailer import send_verification_email, send_reset_email

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        }
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    # Quiet down werkzeug's per-request lines — we keep auth/admin events instead
    "loggers": {
        "werkzeug": {"level": "WARNING", "propagate": True},
    },
})

log = logging.getLogger("doit")

app = Flask(__name__, template_folder="templates")

_secret_file = DATA_DIR / ".secret"
_secret = cfg_get("server", "secret")
if _secret:
    app.secret_key = _secret
elif _secret_file.exists():
    app.secret_key = _secret_file.read_text().strip()
    try:
        os.chmod(_secret_file, 0o600)
    except OSError:
        pass
else:
    _key = os.urandom(24).hex()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _secret_file.write_text(_key)
    os.chmod(_secret_file, 0o600)
    app.secret_key = _key

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=cfg_bool("server", "https"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=cfg_int("server", "session_days", 30)),
)

if cfg_bool("server", "https"):
    # Trust X-Forwarded-For / X-Forwarded-Proto from a single reverse proxy
    # so request.remote_addr and request.url reflect the real client values.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    _behind_proxy = True


# ---------------------------------------------------------------------------
# CSRF helpers
# ---------------------------------------------------------------------------

def generate_csrf() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf() -> bool:
    session_token = session.get("csrf_token", "")
    if not session_token:
        return False
    # JSON endpoints send the token as a request header; forms send it as a field.
    if request.is_json:
        client_token = request.headers.get("X-CSRF-Token", "")
    else:
        client_token = request.form.get("_csrf_token", "")
    if not client_token:
        return False
    return secrets.compare_digest(client_token, session_token)


@app.before_request
def csrf_protect():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    # No session = no authenticated user; let require_login handle the redirect.
    if not session.get("csrf_token"):
        return
    if not validate_csrf():
        abort(403)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_rate_limit: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()
_RATE_LIMIT_MAX_KEYS = 10_000


def _check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Returns True if request is allowed, False if over limit."""
    now = time.time()
    with _rate_limit_lock:
        timestamps = _rate_limit.get(key, [])
        timestamps = [t for t in timestamps if now - t < window_seconds]
        if len(timestamps) >= max_requests:
            _rate_limit[key] = timestamps
            return False
        timestamps.append(now)
        _rate_limit[key] = timestamps
        # Prune stale keys to prevent unbounded growth from spoofed IPs
        if len(_rate_limit) > _RATE_LIMIT_MAX_KEYS:
            cutoff = now - 3600
            stale = [k for k, ts in _rate_limit.items() if not ts or ts[-1] < cutoff]
            for k in stale:
                del _rate_limit[k]
        return True


# True when ProxyFix is active and X-Forwarded-For can be trusted
_behind_proxy: bool = False


def _ip() -> str:
    if _behind_proxy:
        # ProxyFix has already resolved request.remote_addr from X-Forwarded-For
        return request.remote_addr or "unknown"
    return request.remote_addr or "unknown"


def _safe_next(url: str) -> str:
    """Return url only if it's a safe relative path, otherwise the home page."""
    from urllib.parse import urlparse
    if not url or url.startswith("//") or url.startswith("/\\"):
        return url_for("tasks")
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return url_for("tasks")
    return url


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
    # Only generate a CSRF token when a session already exists (authenticated user).
    # Avoid creating sessions (and Set-Cookie) for anonymous visitors.
    csrf_token = generate_csrf() if session.get("user_id") or session.get("csrf_token") else ""
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
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(500)
def internal_error(exc):
    user_id = session.get("user_id", "anonymous")
    log.exception("500 on %s %s (user=%s)", request.method, request.path, user_id)
    return "Internal server error", 500


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

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
                    ok = send_verification_email(email, token, base_url)
                    if not ok:
                        log.error("register: verification email failed for %s", email)
                    log.info("register: new account %s from %s", email, _ip())
                    session["pending_email"] = email
                    return redirect(url_for("verify_pending"))
                except ValueError as exc:
                    log.warning("register: failed for %s from %s — %s", email, _ip(), exc)
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
        log.warning("verify: invalid or expired token used")
        return render_template("verify_pending.html", email="",
                               error="This verification link is invalid or has expired.")
    log.info("verify: email verified for %s", user.get("email"))
    session.pop("pending_email", None)
    session.permanent = True
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_hex(32)
    flash("Your email has been verified. Welcome!")
    return redirect(url_for("tasks"))


@app.route("/auth/resend-verify", methods=["POST"])
def auth_resend_verify():
    email = request.form.get("email", "").strip() or session.get("pending_email", "")
    ip = _ip()
    if email and _check_rate_limit(f"resend:{ip}", 3, 3600) and _check_rate_limit(f"resend:{email}", 3, 3600):
        user = UserStore.get_by_email(email)
        if user and not user.get("verified"):
            token = UserStore.create_verify_token(user["id"])
            base_url = cfg_get("server", "base_url", f"http://127.0.0.1:{cfg_int('server', 'port', 8080)}")
            ok = send_verification_email(email, token, base_url)
            if not ok:
                log.error("resend-verify: email send failed for %s", email)
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
            log.warning("login: rate limit hit from %s", ip)
            error = "Too many login attempts. Please try again later."
        else:
            email    = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            user, err = UserStore.authenticate(email, password)
            if user:
                log.info("login: success for %s from %s", email, ip)
                next_url = _safe_next(request.form.get("next") or request.args.get("next", ""))
                if user.get("totp_secret"):
                    session.clear()
                    session["pending_2fa_user_id"] = user["id"]
                    session["pending_2fa_next"] = next_url
                    return redirect(url_for("auth_2fa"))
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_hex(32)
                return redirect(next_url)
            elif err == "Please verify your email address before signing in.":
                # Don't auto-resend here — that would allow email-bombing unverified
                # accounts via the login form. Direct to the verify page where the
                # user can request a resend with proper rate limiting.
                session["pending_email"] = email
                return redirect(url_for("verify_pending"))
            log.warning("login: failed for %s from %s — %s", email, ip, err)
            error = err
    return render_template("login.html", error=error,
                           next=request.args.get("next", ""))


@app.route("/auth/2fa", methods=["GET", "POST"])
def auth_2fa():
    user_id = session.get("pending_2fa_user_id")
    if not user_id:
        return redirect(url_for("auth_login"))
    error = None
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if UserStore.verify_totp(user_id, code):
            next_url = session.get("pending_2fa_next") or url_for("tasks")
            session.clear()
            session.permanent = True
            session["user_id"] = user_id
            session["csrf_token"] = secrets.token_hex(32)
            return redirect(next_url)
        error = "Invalid code — try again."
    return render_template("2fa.html", error=error)


@app.route("/auth/2fa/setup", methods=["GET", "POST"])
@require_login
def auth_2fa_setup():
    import pyotp
    user = g.user
    error = None
    secret = request.form.get("secret") or request.args.get("secret") or UserStore.generate_totp_secret()
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        totp = pyotp.TOTP(secret)
        if totp.verify(code.replace(" ", ""), valid_window=1):
            UserStore.enable_totp(user["id"], secret)
            log.info("2fa: enabled for %s", user["email"])
            return redirect(url_for("settings") + "?2fa=enabled")
        error = "Code didn't match — please try again."
    issuer = "DoIt"
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name=issuer)
    import qrcode, qrcode.image.svg, io
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    qr_svg = buf.getvalue().decode()
    # Strip XML declaration so it embeds cleanly inline
    qr_svg = qr_svg[qr_svg.index('<svg'):]
    return render_template("2fa_setup.html", secret=secret, uri=uri, qr_svg=qr_svg, error=error)


@app.route("/auth/2fa/disable", methods=["POST"])
@require_login
def auth_2fa_disable():
    if not validate_csrf():
        return "CSRF check failed", 403
    UserStore.disable_totp(g.user["id"])
    log.info("2fa: disabled for %s", g.user["email"])
    return redirect(url_for("settings") + "?2fa=disabled")


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    user = g.get("user")
    if user:
        log.info("logout: %s from %s", user.get("email"), _ip())
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
                    log.info("password-reset: token issued for %s from %s", email, ip)
                    base_url = cfg_get("server", "base_url", f"http://127.0.0.1:{cfg_int('server', 'port', 8080)}")
                    ok = send_reset_email(email, token, base_url)
                    if not ok:
                        log.error("password-reset: email send failed for %s", email)
        else:
            log.warning("password-reset: rate limit hit from %s", ip)
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
                log.info("password-reset: password changed via token from %s", _ip())
                flash("Password reset successfully. Please sign in.")
                return redirect(url_for("auth_login"))
            log.warning("password-reset: invalid or expired token used from %s", _ip())
            error = "This reset link is invalid or has expired."
    return render_template("reset_password.html", token=token, error=error)


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def _get_tasks_file():
    return get_tasks_file(g.user["id"])


def _find_task_by_id(tasks_list: list, task_id: str):
    """Look up a task by its stable #id tag."""
    return next((t for t in tasks_list if t.id == task_id), None)


def _add_task(text: str, section: str = "Inbox") -> None:
    from task_manager import _new_id
    tasks_file = _get_tasks_file()
    if not tasks_file.exists():
        tasks_file.parent.mkdir(parents=True, exist_ok=True)
        tasks_file.write_text("# Tasks\n\n## Inbox\n\n", encoding="utf-8")
    content = tasks_file.read_text(encoding="utf-8")
    new_line = f"- [ ] {text} #id:{_new_id()}"
    section_header = f"## {section}"
    if section_header in content:
        insert_pos = content.index(section_header) + len(section_header)
        content = content[:insert_pos] + f"\n{new_line}" + content[insert_pos:]
    else:
        content = content.rstrip() + f"\n\n{section_header}\n\n{new_line}\n"
    _write_text_atomic(tasks_file, content)


# ---------------------------------------------------------------------------
# Task routes
# ---------------------------------------------------------------------------

@app.route("/tasks")
def tasks_legacy():
    # Old bookmarks/links — redirect to the canonical root URL.
    return redirect(url_for("tasks"))


@app.route("/")
@require_login
def tasks():
    tasks_file   = _get_tasks_file()
    all_tasks    = read_tasks(tasks_file)
    tasks_list   = [t for t in all_tasks if t.section != "Archive"]
    archive_list = [t for t in all_tasks if t.section == "Archive"]
    tasks_list.sort(key=lambda t: t.due or "9999-12-31")
    prefs = UserStore.get_prefs(g.user["id"])
    return render_template("tasks_view.html", tasks=tasks_list,
                           archive_tasks=archive_list,
                           prefs=prefs,
                           all_contexts=get_all_contexts(tasks_file),
                           all_projects=get_all_projects(tasks_file))


@app.route("/tasks/toggle", methods=["POST"])
@require_login
def tasks_toggle():
    data    = request.get_json(silent=True) or {}
    task_id = data.get("line")   # accepts string ID or legacy int index
    action  = data.get("action")
    if task_id is None or action not in ("complete", "reopen"):
        return {"error": "bad request"}, 400
    with get_user_lock(g.user["id"]):
        tasks_file = _get_tasks_file()
        tasks_list = read_tasks(tasks_file)
        task = _find_task_by_id(tasks_list, str(task_id))
        if task is None:
            return {"ok": False}
        if action == "complete":
            task.complete_task()
        else:
            task.reopen_task()
        write_tasks(tasks_list, tasks_file)
        return {"ok": True}


@app.route("/tasks/add", methods=["POST"])
@require_login
def tasks_add():
    data    = request.get_json(silent=True) or {}
    text    = (data.get("text")    or "").strip()
    section = (data.get("section") or "Inbox").strip()
    if not text:
        return {"error": "Empty task"}, 400
    with get_user_lock(g.user["id"]):
        _add_task(text, section)
    return {"ok": True}


@app.route("/tasks/update", methods=["POST"])
@require_login
def tasks_update():
    data      = request.get_json(silent=True) or {}
    task_id   = data.get("task_id")
    field     = data.get("field")
    value     = data.get("value", "").strip()
    task_hash = data.get("task_hash", "")

    if task_id is None or field is None:
        return {"error": "missing task_id or field"}, 400

    with get_user_lock(g.user["id"]):
        tasks_file = _get_tasks_file()
        tasks_list = read_tasks(tasks_file)
        task = _find_task_by_id(tasks_list, str(task_id))
        if task is None:
            return {"error": "task not found"}, 404

        next_task = None

        # Optimistic concurrency check: if the client sent a hash, verify the task
        # hasn't changed since the page was rendered (e.g. edit in another tab).
        if task_hash and task.content_hash != task_hash:
            return {"error": "conflict", "message": "Task has changed — please refresh."}, 409

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
        elif field == "status":
            task.set_status(value if value else None)
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

        extra = []
        if next_task:
            extra.append(next_task.to_line())
            if next_task.raw_notes.strip():
                extra.extend(next_task.raw_notes.split("\n"))
        write_tasks(tasks_list, tasks_file, extra_lines=extra if extra else None)

    result = {"ok": True, "new_hash": task.content_hash}
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
    data        = request.get_json(silent=True) or {}
    action      = data.get("action")
    task_ids    = data.get("task_ids", [])
    value       = data.get("value", "").strip()
    task_hashes = data.get("task_hashes", {})  # {task_id (str): hash}

    if action is None:
        return {"error": "missing action"}, 400

    with get_user_lock(g.user["id"]):
        tasks_file = _get_tasks_file()
        tasks_list = read_tasks(tasks_file)

        # Optimistic concurrency: verify all affected tasks haven't changed.
        if task_hashes:
            for task_id in task_ids:
                task = _find_task_by_id(tasks_list, str(task_id))
                expected = task_hashes.get(str(task_id))
                if task and expected and task.content_hash != expected:
                    return {"error": "conflict",
                            "message": "One or more tasks changed — please refresh."}, 409

        if action == "delete":
            delete_ids = set(str(tid) for tid in task_ids)
            tasks_list = [t for t in tasks_list if t.id not in delete_ids]
        else:
            for task_id in task_ids:
                task = _find_task_by_id(tasks_list, str(task_id))
                if task is None:
                    continue
                if action == "set-priority":
                    task.set_priority(value if value else None)
                elif action == "set-context":
                    task.set_context(value if value else None)
                elif action == "set-due":
                    task.set_due(value if value else None)
                elif action == "set-project":
                    task.set_project(value if value else None)
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
                sort_col  = request.form.get("sort_col",  "due"),
                sort_dir  = request.form.get("sort_dir",  "asc"),
                sort_col2 = request.form.get("sort_col2", ""),
                sort_dir2 = request.form.get("sort_dir2", "asc"),
                sort_col3 = request.form.get("sort_col3", ""),
                sort_dir3 = request.form.get("sort_dir3", "asc"),
            )
            return redirect(url_for("settings", saved="1"))

        elif action == "email":
            new_email = request.form.get("new_email", "").strip().lower()
            try:
                UserStore.change_email(user["id"], new_email)
                token    = UserStore.create_verify_token(user["id"])
                base_url = cfg_get("server", "base_url",
                                   f"http://127.0.0.1:{cfg_int('server','port',8080)}")
                ok = send_verification_email(new_email, token, base_url)
                if not ok:
                    log.error("settings: verification email failed for %s (user %s)", new_email, user["id"])
                    errors["email"] = "Email address updated but we could not send the verification email. Contact support."
                else:
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
        new_state = not user.get("suspended", False)
        UserStore.suspend_user(user_id, new_state)
        action = "suspended" if new_state else "unsuspended"
        log.info("admin: %s %s %s", g.user["email"], action, user["email"])
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/delete", methods=["POST"])
@require_admin
def admin_delete(user_id):
    if request.args.get("confirm") == "yes":
        # Cannot delete self
        if user_id != g.user["id"]:
            target = UserStore.get_user(user_id)
            UserStore.delete_user(user_id)
            log.info("admin: %s deleted user %s", g.user["email"],
                     target["email"] if target else user_id)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/toggle-admin", methods=["POST"])
@require_admin
def admin_toggle_admin(user_id):
    # Cannot demote self
    if user_id == g.user["id"]:
        return redirect(url_for("admin_users"))
    target = UserStore.get_user(user_id)
    UserStore.toggle_admin(user_id)
    if target:
        target_after = UserStore.get_user(user_id)
        new_role = "admin" if target_after and target_after.get("admin") else "user"
        log.info("admin: %s set %s role to %s", g.user["email"], target["email"], new_role)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/resend-verify", methods=["POST"])
@require_admin
def admin_resend_verify(user_id):
    user = UserStore.get_user(user_id)
    if user and not user.get("verified"):
        token = UserStore.create_verify_token(user_id)
        base_url = cfg_get("server", "base_url", f"http://127.0.0.1:{cfg_int('server', 'port', 8080)}")
        ok = send_verification_email(user["email"], token, base_url)
        if not ok:
            log.error("admin: resend-verify email failed for %s (user %s)", user["email"], user_id)
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
        log.info("No users yet — register at http://%s:%s/register", host, port)

    log.info("Starting doit on http://%s:%s (debug=%s, data=%s)", host, port, debug, DATA_DIR)
    app.run(host=host, port=port, debug=debug)
