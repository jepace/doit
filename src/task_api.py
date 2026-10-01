#!/usr/bin/env python3
"""
Task operations for programmatic clients (the REST API and the MCP server).

Both front ends call into this module, so they behave identically and there's
one place to validate input. Everything here takes an explicit user_id — it
never reads Flask's session — and every mutation runs under that user's lock.

Validation is deliberately strict. tasks.md is a line-oriented text format with
inline "#tag:value" metadata, so an unchecked value containing a newline, a
space, or a "#" could forge extra tasks, smuggle in tags, or silently drop
text on the next rewrite. Agents get a clear error instead.
"""

import re
import sys
import textwrap
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from task_manager import (read_tasks, write_tasks, write_archive, insert_task_line,
                          get_tasks_file, get_archive_file, sort_tasks,
                          _render_task_lines)
from user_store import get_user_lock


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------

PRIORITIES = ("top", "high", "medium", "low")
STATUSES   = ("waiting", "blocked", "in-progress", "someday")
_WEEKDAYS  = "mon|tue|wed|thu|fri|sat|sun"
_REP_RE    = re.compile(rf"^(\d{{1,3}}[dwmy]\+?|({_WEEKDAYS})(,({_WEEKDAYS}))*)$")
_DATE_RE   = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WORD_RE   = re.compile(r"^[\w.\-/&+]{1,40}$")        # context / project
_SECTION_RE = re.compile(r"^[\w .\-]{1,40}$")
MAX_DESCRIPTION = 500
MAX_NOTES       = 20_000
MAX_LIST        = 500

# Fields an agent may set, mapped to the setter on Task.
_SETTABLE = ("description", "due", "start", "priority", "context", "project",
             "recurrence", "status", "starred", "notes")


def _err(field: str, msg: str):
    return ApiError(400, "invalid_field", f"{field}: {msg}")


def _clean_text(value) -> str:
    return "".join(ch for ch in str(value) if ch == "\t" or ord(ch) >= 0x20).strip()


def _validate(field: str, value):
    """Return the normalised value for `field`, or None to clear it."""
    if field == "starred":
        if not isinstance(value, bool):
            raise _err(field, "must be true or false")
        return value
    if value is None or value == "":
        if field == "description":
            raise _err(field, "is required")
        return None
    if not isinstance(value, str):
        raise _err(field, "must be a string or null")

    if field == "description":
        v = _clean_text(value)
        if "\n" in value or "\r" in value:
            raise _err(field, "must be a single line (put extra detail in notes)")
        if re.search(r"#\w", v):
            raise _err(field, "may not contain #tags — use the dedicated fields instead")
        if not v:
            raise _err(field, "is required")
        if len(v) > MAX_DESCRIPTION:
            raise _err(field, f"must be at most {MAX_DESCRIPTION} characters")
        return v
    if field == "notes":
        v = value.replace("\r\n", "\n").replace("\r", "\n")
        v = "".join(ch for ch in v if ch in "\n\t" or ord(ch) >= 0x20)
        if len(v) > MAX_NOTES:
            raise _err(field, f"must be at most {MAX_NOTES} characters")
        return v
    if field in ("due", "start"):
        if not _DATE_RE.match(value):
            raise _err(field, "must be a date in YYYY-MM-DD form")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise _err(field, "is not a real calendar date")
        return value
    if field == "priority":
        v = value.lower()
        if v not in PRIORITIES:
            raise _err(field, f"must be one of {', '.join(PRIORITIES)}")
        return v
    if field == "status":
        v = value.lower()
        if v not in STATUSES:
            raise _err(field, f"must be one of {', '.join(STATUSES)}")
        return v
    if field == "recurrence":
        v = value.lower().replace(" ", "")
        if not _REP_RE.match(v):
            raise _err(field, "must look like 1d, 2w, 1m, 1y (append + to repeat "
                              "from completion date) or weekdays like mon,wed,fri")
        return v
    if field in ("context", "project"):
        if not _WORD_RE.match(value):
            raise _err(field, "must be 1-40 characters with no spaces or '#'")
        return value
    raise _err(field, "is not a recognised field")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def serialize(task) -> dict:
    return {
        "id":           task.id,
        "description":  task.description,
        "done":         bool(task.complete),
        "completed_on": task.tags.get("#done"),
        "due":          task.due,
        "start":        task.start,
        "priority":     task.priority,
        "context":      task.context,
        "project":      task.project,
        "recurrence":   task.recurrence,
        "status":       task.status,
        "starred":      "#star" in task.tags,
        # Stored note lines carry a structural indent; hand back the text as
        # written so an agent's read -> edit -> write round-trip is lossless.
        "notes":        textwrap.dedent(task.raw_notes).strip("\n"),
        "section":      task.section,
        "version":      task.content_hash,
    }


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _load(user_id: str, task_id: str):
    """(task, owning_list, in_archive). Active file first: cheap common case."""
    if not isinstance(task_id, str) or not re.fullmatch(r"[a-f0-9]{1,32}", task_id):
        raise ApiError(404, "not_found", "No task with that id.")
    active = read_tasks(get_tasks_file(user_id))
    t = next((x for x in active if x.id == task_id), None)
    if t is not None:
        return t, active, False
    archived = read_tasks(get_archive_file(user_id))
    t = next((x for x in archived if x.id == task_id), None)
    if t is not None:
        return t, archived, True
    raise ApiError(404, "not_found", "No task with that id.")


def _check_version(task, expected):
    if expected and task.content_hash != expected:
        raise ApiError(409, "conflict",
                       "The task changed since you read it. Fetch it again and retry.")


def _save(user_id: str, tasks_list: list, in_archive: bool, extra_lines=None):
    tasks_file, archive_file = get_tasks_file(user_id), get_archive_file(user_id)
    if in_archive:
        write_archive(tasks_list, archive_file, tasks_file)
        if extra_lines:
            from task_manager import append_to_tasks
            append_to_tasks(extra_lines, tasks_file)
    else:
        write_tasks(tasks_list, tasks_file, extra_lines=extra_lines or None,
                    archive_file=archive_file)


def _to_storage_notes(text: str) -> str:
    """Give every note line the same structural two-space indent.

    tasks.md marks note lines by indentation; indenting only the lines that
    aren't already indented (as the writer otherwise would) flattens nested
    content like subtasks. A uniform indent lets serialize() dedent back to
    exactly what was written.
    """
    return "\n".join(("  " + ln) if ln.strip() else "" for ln in (text or "").split("\n"))


def _apply(task, field: str, value):
    if field == "description":
        task.description = value
    elif field == "notes":
        task.set_notes(_to_storage_notes(value))
    elif field == "starred":
        if value:
            task.tags["#star"] = None
        else:
            task.tags.pop("#star", None)
    else:
        {"due": task.set_due, "start": task.set_start, "priority": task.set_priority,
         "context": task.set_context, "project": task.set_project,
         "recurrence": task.set_recurrence, "status": task.set_status}[field](value)


def _normalise_fields(fields: dict, *, creating: bool) -> dict:
    if not isinstance(fields, dict):
        raise ApiError(400, "invalid_request", "Expected a JSON object of fields.")
    unknown = set(fields) - set(_SETTABLE) - ({"section"} if creating else set())
    if unknown:
        raise ApiError(400, "invalid_field",
                       f"Unknown field(s): {', '.join(sorted(unknown))}. "
                       f"Settable: {', '.join(_SETTABLE)}.")
    return {k: _validate(k, v) for k, v in fields.items() if k != "section"}


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def list_tasks(user_id: str, *, status: str = "open", context=None, project=None,
               priority=None, due_before=None, due_after=None, search=None,
               limit: int = 100) -> dict:
    if status not in ("open", "done", "all"):
        raise _err("status", "must be open, done or all")
    for name, val in (("due_before", due_before), ("due_after", due_after)):
        if val is not None:
            _validate("due", val)  # reuse date check; error names 'due'
    try:
        limit = max(1, min(int(limit), MAX_LIST))
    except (TypeError, ValueError):
        raise _err("limit", "must be an integer")

    tasks = []
    if status in ("open", "all"):
        tasks += [t for t in read_tasks(get_tasks_file(user_id)) if not t.complete]
    if status in ("done", "all"):
        tasks += read_tasks(get_archive_file(user_id))

    def keep(t):
        if context and (t.context or "").lower() != context.lower():
            return False
        if project and (t.project or "").lower() != project.lower():
            return False
        if priority and t.priority != priority.lower():
            return False
        if due_before and not (t.due and t.due <= due_before):
            return False
        if due_after and not (t.due and t.due >= due_after):
            return False
        if search:
            hay = f"{t.description}\n{t.notes}".lower()
            if search.lower() not in hay:
                return False
        return True

    matched = sort_tasks([t for t in tasks if keep(t)], {})
    return {"tasks": [serialize(t) for t in matched[:limit]],
            "total": len(matched), "truncated": len(matched) > limit}


def get_task(user_id: str, task_id: str) -> dict:
    task, _, _ = _load(user_id, task_id)
    return serialize(task)


def add_task(user_id: str, fields: dict) -> dict:
    values = _normalise_fields(fields, creating=True)
    if "description" not in values:
        raise _err("description", "is required")
    section = fields.get("section") or "Inbox"
    if not isinstance(section, str) or not _SECTION_RE.match(section) \
            or section.strip().lower() == "archive":
        raise _err("section", "must be a simple name (letters, digits, spaces) and not 'Archive'")

    # Build the line via a Task so tag formatting matches everywhere else.
    from task_manager import Task
    t = Task("- [ ] placeholder", -1)
    for k, v in values.items():
        _apply(t, k, v)
    t.tags.pop("#id", None)
    t.tags.setdefault("#start", date.today().isoformat())
    line = t.to_line()[len("- [ ] "):]
    with get_user_lock(user_id):
        new_id = insert_task_line(get_tasks_file(user_id), line,
                                  section.strip(), _to_storage_notes(values.get("notes") or ""))
    return get_task(user_id, new_id)


def update_task(user_id: str, task_id: str, fields: dict, expected_version=None) -> dict:
    values = _normalise_fields(fields, creating=False)
    if not values:
        raise ApiError(400, "invalid_request", "No fields to update.")
    with get_user_lock(user_id):
        task, lst, in_archive = _load(user_id, task_id)
        _check_version(task, expected_version)
        for k, v in values.items():
            _apply(task, k, v)
        _save(user_id, lst, in_archive)
    return get_task(user_id, task_id)


def complete_task(user_id: str, task_id: str, expected_version=None) -> dict:
    with get_user_lock(user_id):
        task, lst, in_archive = _load(user_id, task_id)
        _check_version(task, expected_version)
        if task.complete:
            return {"task": serialize(task), "next_occurrence": None}
        task.complete_task()
        nxt = task.get_next_recurrence()
        extra = _render_task_lines(nxt) if nxt else None
        _save(user_id, lst, in_archive, extra)
    return {"task": get_task(user_id, task_id),
            "next_occurrence": get_task(user_id, nxt.id) if nxt else None}


def reopen_task(user_id: str, task_id: str, expected_version=None) -> dict:
    with get_user_lock(user_id):
        task, lst, in_archive = _load(user_id, task_id)
        _check_version(task, expected_version)
        if task.complete:
            task.reopen_task()
            _save(user_id, lst, in_archive)
    return get_task(user_id, task_id)


def delete_task(user_id: str, task_id: str, expected_version=None) -> dict:
    with get_user_lock(user_id):
        task, lst, in_archive = _load(user_id, task_id)
        _check_version(task, expected_version)
        remaining = [t for t in lst if t.id != task_id]
        _save(user_id, remaining, in_archive)
    return {"deleted": task_id}


def list_contexts(user_id: str) -> dict:
    tasks = [t for t in read_tasks(get_tasks_file(user_id)) if not t.complete]
    return {"contexts": sorted({t.context for t in tasks if t.context}, key=str.lower),
            "projects": sorted({t.project for t in tasks if t.project}, key=str.lower)}
