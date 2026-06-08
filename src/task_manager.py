#!/usr/bin/env python3
"""
Task manager for wiki/tasks.md. Parses, filters, and updates tasks.
"""

import hashlib
import os
import re
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR  = REPO_ROOT / "data"



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
        tags_str = ' '.join(
            f"{k}:{v}" if v is not None else k
            for k, v in sorted(self.tags.items())
        )
        line = f"{self.indent}- [{checkbox}] {self.description}"
        if tags_str:
            line += f" {tags_str}"
        return line

    # ── Properties — keys WITHOUT colon ──────────────────────
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

        # Create new task for next occurrence
        next_task = Task("", -1)
        next_task.indent = self.indent
        next_task.complete = False
        next_task.description = self.description
        next_task.tags = self.tags.copy()
        next_task.tags.pop('#done', None)  # Remove done tag
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
            tasks.append(task)
        else:
            i += 1

    return tasks


def write_tasks(tasks, tasks_file: Path, extra_lines: list | None = None):
    """Write updated tasks back to the given tasks file.

    Completed tasks are moved to an ## Archive section at the end of the file.
    extra_lines: optional list of raw lines to insert before the Archive section
    (used to add a new recurrence task in the same atomic write).
    """
    if not tasks_file.exists():
        return

    lines = tasks_file.read_text(encoding='utf-8').split('\n')
    task_map = {t.line_num: t for t in tasks}

    # Separate lines into pre-archive body and existing archive section
    archive_start = None
    for idx, line in enumerate(lines):
        if re.match(r'^## Archive\s*$', line, re.IGNORECASE):
            archive_start = idx
            break

    body_lines = lines[:archive_start] if archive_start is not None else lines

    # Rewrite the main body; collect newly-completed tasks for the archive
    new_body = []
    newly_archived = []  # list of (task, notes_str) to move to archive
    i = 0
    task_count = 0

    while i < len(body_lines):
        line = body_lines[i]

        if re.match(r'^\s*- \[[x ]\]', line):
            if task_count in task_map:
                task = task_map[task_count]
                if task.complete:
                    # Move to archive instead of writing in place
                    newly_archived.append(task)
                else:
                    new_body.append(task.to_line())
                    if task.raw_notes.strip():
                        for note_line in task.raw_notes.split('\n'):
                            if note_line.strip() and note_line[:1] not in (' ', '\t'):
                                note_line = '  ' + note_line
                            new_body.append(note_line)
            task_count += 1
            i += 1
            # Skip old inline notes
            while i < len(body_lines):
                nxt = body_lines[i]
                if re.match(r'^##', nxt) or re.match(r'^\s*- \[[x ]\]', nxt):
                    break
                i += 1
            continue

        new_body.append(line)
        i += 1

    # Append extra_lines (new recurrence task) into body before archive
    if extra_lines:
        new_body.extend(extra_lines)

    # Process existing archive section through task_map so that reopened tasks
    # move back to the body and updated tasks get their new content written.
    existing_archive_entries = []
    reopened_from_archive = []
    if archive_start is not None:
        archive_lines = lines[archive_start + 1:]
        j = 0
        while j < len(archive_lines):
            aline = archive_lines[j]
            if re.match(r'^\s*- \[[x ]\]', aline):
                if task_count in task_map:
                    task = task_map[task_count]
                    if task.complete:
                        # Still done — keep in archive with updated content
                        entry = [task.to_line()]
                        if task.raw_notes.strip():
                            for note_line in task.raw_notes.split('\n'):
                                if note_line.strip() and note_line[:1] not in (' ', '\t'):
                                    note_line = '  ' + note_line
                                entry.append(note_line)
                        existing_archive_entries.extend(entry)
                    else:
                        # Reopened — move back to body
                        body_entry = [task.to_line()]
                        if task.raw_notes.strip():
                            for note_line in task.raw_notes.split('\n'):
                                if note_line.strip() and note_line[:1] not in (' ', '\t'):
                                    note_line = '  ' + note_line
                                body_entry.append(note_line)
                        reopened_from_archive.extend(body_entry)
                task_count += 1
                j += 1
                # Skip old inline notes in archive
                while j < len(archive_lines):
                    nxt = archive_lines[j]
                    if re.match(r'^##', nxt) or re.match(r'^\s*- \[[x ]\]', nxt):
                        break
                    j += 1
                continue
            existing_archive_entries.append(aline)
            j += 1

    # Reopened tasks go back into the body (before archive)
    if reopened_from_archive:
        new_body.extend(reopened_from_archive)

    # Render newly completed tasks
    new_archive_entries = []
    for task in newly_archived:
        new_archive_entries.append(task.to_line())
        if task.raw_notes.strip():
            for note_line in task.raw_notes.split('\n'):
                if note_line.strip() and note_line[:1] not in (' ', '\t'):
                    note_line = '  ' + note_line
                new_archive_entries.append(note_line)

    # Assemble final file
    # Strip trailing blank lines from body so we control spacing
    while new_body and not new_body[-1].strip():
        new_body.pop()

    final_lines = new_body

    has_archive = new_archive_entries or existing_archive_entries
    if has_archive:
        final_lines.append('')
        final_lines.append('## Archive')
        final_lines.append('')
        # New completions go at the top of the archive (most recent first)
        final_lines.extend(new_archive_entries)
        final_lines.extend(existing_archive_entries)
    else:
        final_lines.append('')  # ensure trailing newline

    _write_text_atomic(tasks_file, '\n'.join(final_lines))


def get_all_contexts(tasks_file: Path):
    return sorted(set(t.context for t in read_tasks(tasks_file) if t.context))

def get_all_projects(tasks_file: Path):
    return sorted(set(t.project for t in read_tasks(tasks_file) if t.project))

def get_all_sections(tasks_file: Path):
    return sorted(set(t.section for t in read_tasks(tasks_file) if t.section))
