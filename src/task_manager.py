#!/usr/bin/env python3
"""
Task manager for wiki/tasks.md. Parses, filters, and updates tasks.
"""

import hashlib
import os
import re
import secrets
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR  = REPO_ROOT / "data"



def _new_id() -> str:
    """Generate a short random task ID (6 hex chars)."""
    return secrets.token_hex(3)


def get_tasks_file(user_id: str) -> Path:
    """Return the tasks.md path for the given user."""
    return DATA_DIR / user_id / "tasks.md"


def _write_text_atomic(path: Path, content: str) -> None:
    """Write content to path via a temp file so a crash never leaves a partial write."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


class Task:
    """Represents a single task with all its metadata."""

    def __init__(self, line, line_num, raw_notes=""):
        self.line_num = line_num
        self.raw_notes = raw_notes
        self.section = None

        match = re.match(r'^(\s*)- \[([x ])\] (.+)$', line)
        if not match:
            self.indent = ""
            self.complete = False
            self.description = ""
            self.tags = {}
            return

        self.indent = match.group(1)
        self.complete = match.group(2) == 'x'
        rest = match.group(3)

        # Split description from tags
        tag_pattern = r'#\w+(?::[^\s]*)?'
        tag_matches = list(re.finditer(tag_pattern, rest))

        if tag_matches:
            self.description = rest[:tag_matches[0].start()].strip()
            tags_text = rest[tag_matches[0].start():]
        else:
            self.description = rest.strip()
            tags_text = ""

        # Parse tags — keys stored WITHOUT colon: '#p' -> 'high'
        self.tags = {}
        for m in re.finditer(tag_pattern, tags_text):
            tag_full = m.group(0)
            if ':' in tag_full:
                key, val = tag_full.split(':', 1)
                self.tags[key] = val.lstrip(':')  # strip extra colons from old corrupted data
            else:
                self.tags[tag_full] = None  # e.g. '#star': None

    def to_line(self):
        """Reconstruct markdown task line."""
        checkbox = 'x' if self.complete else ' '
        # Sort tags; put #id last so it doesn't clutter the readable part
        tags_str = ' '.join(
            f"{k}:{v}" if v is not None else k
            for k, v in sorted(self.tags.items(), key=lambda kv: (kv[0] == '#id', kv[0]))
        )
        line = f"{self.indent}- [{checkbox}] {self.description}"
        if tags_str:
            line += f" {tags_str}"
        return line

    # ── Properties — keys WITHOUT colon ──────────────────────
    @property
    def id(self):         return self.tags.get('#id')

    @property
    def due(self):        return self.tags.get('#due')

    @property
    def priority(self):   return self.tags.get('#p')

    @property
    def context(self):    return self.tags.get('#ctx')

    @property
    def project(self):    return self.tags.get('#proj')

    @property
    def status(self):     return self.tags.get('#s')

    @property
    def recurrence(self): return self.tags.get('#rep')

    @property
    def start(self):      return self.tags.get('#start')

    @property
    def notes(self):      return self.raw_notes.strip()

    # ── Setters — keys WITHOUT colon ─────────────────────────
    def _set(self, key, val):
        if val:
            self.tags[key] = val
        else:
            self.tags.pop(key, None)

    @property
    def content_hash(self) -> str:
        """Short hash of the rendered task line — used for optimistic concurrency checks."""
        return hashlib.md5(self.to_line().encode()).hexdigest()[:8]

    def set_id(self, val):         self._set('#id', val)
    def set_due(self, val):        self._set('#due', val)
    def set_priority(self, val):   self._set('#p', val)
    def set_context(self, val):    self._set('#ctx', val)
    def set_project(self, val):    self._set('#proj', val)
    def set_status(self, val):     self._set('#s', val)
    def set_recurrence(self, val): self._set('#rep', val)
    def set_start(self, val):      self._set('#start', val)
    def set_notes(self, val):      self.raw_notes = val

    def complete_task(self):
        self.complete = True
        self.tags['#done'] = datetime.now().strftime('%Y-%m-%d')

    def reopen_task(self):
        self.complete = False
        self.tags.pop('#done', None)

    def get_next_recurrence(self):
        """If task is recurring, return a new Task for the next occurrence."""
        if not self.recurrence or not self.complete:
            return None

        rep = self.recurrence.lower()

        # Weekday recurrence: e.g. "fri" or "mon,wed,fri".
        # Always relative to the completion date — advance to the next
        # occurrence of the nearest listed weekday (strictly after today).
        WEEKDAYS = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3,
                    'fri': 4, 'sat': 5, 'sun': 6}
        parts = [p.strip() for p in rep.split(',') if p.strip()]
        if parts and all(p in WEEKDAYS for p in parts):
            today = datetime.now().date()
            targets = {WEEKDAYS[p] for p in parts}
            # days ahead to the next matching weekday (1..7, never 0/today)
            ahead = min((wd - today.weekday()) % 7 or 7 for wd in targets)
            next_due = today + timedelta(days=ahead)
            return self._build_next_occurrence(next_due)

        # Parse recurrence: e.g. "1d", "1w+", "2m", etc.
        match = re.match(r'^(\d+)([dwmy])(\+?)$', rep)
        if not match:
            return None

        count = int(match.group(1))
        unit = match.group(2)
        relative = match.group(3) == '+'  # '+' means relative to completion date

        # Get base date
        if relative:
            base = datetime.now().date()
        else:
            try:
                base = datetime.fromisoformat(self.due).date() if self.due else datetime.now().date()
            except ValueError:
                base = datetime.now().date()

        # Calculate next due date
        if unit == 'd':
            next_due = base + timedelta(days=count)
        elif unit == 'w':
            next_due = base + timedelta(weeks=count)
        elif unit == 'm':
            # Month calculation
            month = base.month + count
            year = base.year
            while month > 12:
                month -= 12
                year += 1
            try:
                next_due = base.replace(year=year, month=month)
            except ValueError:  # Day doesn't exist in target month — clamp to last day
                overflow_month = month + 1
                overflow_year  = year
                if overflow_month > 12:
                    overflow_month = 1
                    overflow_year += 1
                next_due = date(overflow_year, overflow_month, 1) - timedelta(days=1)
        elif unit == 'y':
            next_due = base.replace(year=base.year + count)
        else:
            return None

        return self._build_next_occurrence(next_due)

    def _build_next_occurrence(self, next_due):
        """Create a fresh, incomplete copy of this task due on next_due."""
        next_task = Task("", -1)
        next_task.indent = self.indent
        next_task.complete = False
        next_task.description = self.description
        next_task.tags = self.tags.copy()
        next_task.tags.pop('#done', None)  # Remove done tag
        next_task.tags['#id'] = _new_id()  # Fresh identity for new occurrence
        next_task.tags['#due'] = next_due.isoformat()
        next_task.section = self.section
        next_task.raw_notes = self.raw_notes  # Preserve notes from completed task

        return next_task


def read_tasks(tasks_file: Path):
    """Read all tasks from the given tasks file."""
    if not tasks_file.exists():
        return []

    lines = tasks_file.read_text(encoding='utf-8').split('\n')
    tasks = []
    current_section = None
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith('##'):
            current_section = line.lstrip('#').strip()
            i += 1
            continue

        if re.match(r'^\s*- \[[x ]\]', line):
            notes_lines = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                # Only break on top-level (non-indented) task lines so that
                # indented checkboxes (subtasks) are preserved as note content.
                if re.match(r'^##', nxt) or re.match(r'^- \[[x ]\]', nxt):
                    break
                if nxt and (nxt[0] in ' \t' or not nxt.strip()):
                    notes_lines.append(nxt)
                    i += 1
                else:
                    break

            task = Task(line, len(tasks), '\n'.join(notes_lines))
            task.section = current_section
            if not task.id:
                task.tags['#id'] = _new_id()  # ephemeral until next write_tasks
            tasks.append(task)
        else:
            i += 1

    return tasks


def _render_task_lines(task: 'Task') -> list[str]:
    """Render a task and its notes as a list of lines."""
    lines = [task.to_line()]
    if task.raw_notes.strip():
        for note_line in task.raw_notes.split('\n'):
            if note_line.strip() and note_line[:1] not in (' ', '\t'):
                note_line = '  ' + note_line
            lines.append(note_line)
    return lines


def _lookup_task(line: str, task_count: int,
                 id_map: dict, pos_map: dict) -> 'Task | None':
    """Find the Task object for a file line using #id tag (stable) or position (fallback)."""
    m = re.search(r'#id:([a-f0-9]+)', line)
    if m:
        return id_map.get(m.group(1))
    return pos_map.get(task_count)


def write_tasks(tasks, tasks_file: Path, extra_lines: list | None = None):
    """Write updated tasks back to the given tasks file.

    Tasks are matched by their #id tag (stable across reorders).
    Tasks without an #id get one assigned now (one-time migration).
    Completed tasks move to ## Archive; reopened archive tasks return to body.
    extra_lines: raw lines appended to body before Archive (for new recurrences).
    """
    if not tasks_file.exists():
        return

    # Ensure every task has a stable ID before we write anything
    for t in tasks:
        if not t.id:
            t.tags['#id'] = _new_id()

    id_map  = {t.id: t       for t in tasks if t.id}
    pos_map = {t.line_num: t for t in tasks}  # fallback for lines with no #id yet

    lines = tasks_file.read_text(encoding='utf-8').split('\n')

    # Split file into body and existing archive section
    archive_start = None
    for idx, line in enumerate(lines):
        if re.match(r'^## Archive\s*$', line, re.IGNORECASE):
            archive_start = idx
            break

    body_lines    = lines[:archive_start] if archive_start is not None else lines
    archive_lines = lines[archive_start + 1:] if archive_start is not None else []

    def _process_section(section_lines, task_count_start):
        """Iterate lines, rewrite tasks via map. Returns (new_lines, newly_archived, reopened, task_count)."""
        out = []
        archived = []
        reopened = []
        task_count = task_count_start
        i = 0
        while i < len(section_lines):
            line = section_lines[i]
            if re.match(r'^\s*- \[[x ]\]', line):
                task = _lookup_task(line, task_count, id_map, pos_map)
                if task is not None:
                    if task.complete:
                        archived.append(task)
                    else:
                        out.extend(_render_task_lines(task))
                else:
                    # Task was deleted — omit it
                    pass
                task_count += 1
                i += 1
                # Skip original inline notes (rewritten from task.raw_notes)
                while i < len(section_lines):
                    nxt = section_lines[i]
                    if re.match(r'^##', nxt) or re.match(r'^- \[[x ]\]', nxt):
                        break
                    i += 1
                continue
            out.append(line)
            i += 1
        return out, archived, reopened, task_count

    new_body, newly_archived, _, task_count = _process_section(body_lines, 0)

    if extra_lines:
        new_body.extend(extra_lines)

    # Process archive section: keep completed, move reopened to body
    existing_archive_out = []
    reopened_from_archive = []
    if archive_lines:
        i = 0
        while i < len(archive_lines):
            line = archive_lines[i]
            if re.match(r'^\s*- \[[x ]\]', line):
                task = _lookup_task(line, task_count, id_map, pos_map)
                if task is not None:
                    if task.complete:
                        existing_archive_out.extend(_render_task_lines(task))
                    else:
                        reopened_from_archive.extend(_render_task_lines(task))
                task_count += 1
                i += 1
                while i < len(archive_lines):
                    nxt = archive_lines[i]
                    if re.match(r'^##', nxt) or re.match(r'^- \[[x ]\]', nxt):
                        break
                    i += 1
                continue
            existing_archive_out.append(line)
            i += 1

    if reopened_from_archive:
        new_body.extend(reopened_from_archive)

    new_archive_entries = []
    for task in newly_archived:
        new_archive_entries.extend(_render_task_lines(task))

    # Assemble
    while new_body and not new_body[-1].strip():
        new_body.pop()

    final_lines = new_body

    has_archive = new_archive_entries or existing_archive_out
    if has_archive:
        final_lines += ['', '## Archive', '']
        final_lines.extend(new_archive_entries)
        final_lines.extend(existing_archive_out)
    else:
        final_lines.append('')

    _write_text_atomic(tasks_file, '\n'.join(final_lines))


def get_all_contexts(tasks_file: Path):
    return sorted(set(t.context for t in read_tasks(tasks_file) if t.context))

def get_all_projects(tasks_file: Path):
    return sorted(set(t.project for t in read_tasks(tasks_file) if t.project))

def get_all_sections(tasks_file: Path):
    return sorted(set(t.section for t in read_tasks(tasks_file) if t.section))


_PRI_ORDER = {"top": 0, "high": 1, "medium": 2, "low": 3}

class _Desc:
    """Proxy to reverse string comparison for descending sorts."""
    __slots__ = ("s",)
    def __init__(self, s): self.s = s
    def __lt__(self, o): return self.s > o.s
    def __gt__(self, o): return self.s < o.s
    def __eq__(self, o): return self.s == o.s
    def __le__(self, o): return self.s >= o.s
    def __ge__(self, o): return self.s <= o.s

def _col_value(task, col):
    if col == "due":         return task.due or "9999-12-31"
    if col == "priority":    return _PRI_ORDER.get(task.priority or "", 4)
    if col == "context":     return (task.context or "").lower()
    if col == "description": return (task.description or "").lower()
    if col == "start":       return task.start or "9999-12-31"
    return ""

def sort_tasks(tasks: list, prefs: dict) -> list:
    """Return a new list sorted by the user's saved sort prefs (up to 3 levels).

    Mirrors the client-side sortTable() in tasks_view.html, including its
    implicit tiebreak (due->priority, priority->due, else description) so the
    print view — which has no client-side JS re-sort — matches the on-screen
    order exactly.
    """
    levels = [
        (prefs.get("sort_col",  "due"), prefs.get("sort_dir",  "asc")),
        (prefs.get("sort_col2", ""),    prefs.get("sort_dir2", "asc")),
        (prefs.get("sort_col3", ""),    prefs.get("sort_dir3", "asc")),
    ]
    levels = [(col, d) for col, d in levels if col]
    if not levels:
        levels = [("due", "asc")]

    used_cols = {col for col, _ in levels}
    primary_col = levels[0][0]
    if primary_col == "due" and "priority" not in used_cols:
        levels.append(("priority", "asc"))
    elif primary_col == "priority" and "due" not in used_cols:
        levels.append(("due", "asc"))
    elif primary_col not in ("due", "priority") and "description" not in used_cols:
        levels.append(("description", "asc"))

    def key(t):
        parts = []
        for col, d in levels:
            v = _col_value(t, col)
            if d != "asc":
                v = -v if isinstance(v, int) else _Desc(v)
            parts.append(v)
        return tuple(parts)

    return sorted(tasks, key=key)
