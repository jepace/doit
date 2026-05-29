#!/usr/bin/env python3
"""
doit — Task manager web server

Usage:
  cp config.json.example config.json   # set password
  python3 tools/serve.py               # http://127.0.0.1:8080
"""

import functools
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    from flask import (Flask, abort, redirect, render_template,
                       request, session, url_for)
except ImportError:
    sys.exit("Error: flask not installed. Run: pip install flask")

sys.path.insert(0, str(Path(__file__).parent))

from config import cfg_get, cfg_bool, cfg_int
from task_manager import read_tasks, write_tasks, get_all_contexts, get_all_projects, TASKS_FILE

REPO_ROOT  = Path(__file__).resolve().parent.parent
WIKI_DIR   = REPO_ROOT / "wiki"
PASSWD_FILE = WIKI_DIR / ".passwd"

app = Flask(__name__, template_folder="templates")

_secret_file = WIKI_DIR / ".secret"
_secret = cfg_get("server", "secret")
if _secret:
    app.secret_key = _secret
elif _secret_file.exists():
    app.secret_key = _secret_file.read_text().strip()
else:
    _key = os.urandom(24).hex()
    _secret_file.write_text(_key)
    app.secret_key = _key

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=cfg_bool("server", "https"),
)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def user_exists() -> bool:
    return PASSWD_FILE.exists()


def create_user(password: str) -> None:
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    PASSWD_FILE.write_text(_hash_password(password))


def authenticate(password: str) -> bool:
    if not PASSWD_FILE.exists():
        return False
    return PASSWD_FILE.read_text().strip() == _hash_password(password)


def require_login(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not user_exists():
            return redirect(url_for("setup"))
        if not session.get("logged_in"):
            return redirect(url_for("auth_login", next=request.full_path.rstrip("?")))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if user_exists():
        return redirect(url_for("auth_login"))
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if not password:
            error = "Password is required."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        else:
            create_user(password)
            session["logged_in"] = True
            return redirect(url_for("tasks"))
    return render_template("setup.html", error=error)


@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    if not user_exists():
        return redirect(url_for("setup"))
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if authenticate(password):
            session["logged_in"] = True
            next_url = request.form.get("next") or request.args.get("next") or url_for("tasks")
            return redirect(next_url)
        error = "Incorrect password."
    return render_template("login.html", error=error,
                           next=request.args.get("next", ""))


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))


@app.route("/")
def index():
    return redirect(url_for("tasks"))


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def _toggle_task(line_num: int, action: str) -> bool:
    tasks_file = WIKI_DIR / "tasks.md"
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
    tasks_file = WIKI_DIR / "tasks.md"
    if not tasks_file.exists():
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
    tasks_list = read_tasks()
    tasks_list.sort(key=lambda t: t.due or "9999-12-31")
    return render_template("tasks_view.html", tasks=tasks_list,
                           all_contexts=get_all_contexts(),
                           all_projects=get_all_projects())


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

    tasks_list = read_tasks()
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

    write_tasks(tasks_list)

    if next_task:
        next_line  = next_task.to_line()
        next_notes = next_task.raw_notes.strip()
        with open(TASKS_FILE, "a", encoding="utf-8") as f:
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

    tasks_list = read_tasks()

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

    write_tasks(tasks_list)
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
