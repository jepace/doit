"""
Unit tests for task_manager.py — parsing, serialization, and recurrence.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from task_manager import Task, read_tasks, write_tasks


# ---------------------------------------------------------------------------
# Task parsing
# ---------------------------------------------------------------------------

class TestTaskParsing:
    def test_basic_open_task(self):
        t = Task("- [ ] Buy milk", 0)
        assert t.description == "Buy milk"
        assert t.complete is False
        assert t.tags == {}

    def test_basic_complete_task(self):
        t = Task("- [x] Done thing", 0)
        assert t.complete is True
        assert t.description == "Done thing"

    def test_all_tags(self):
        t = Task("- [ ] Task #p:high #due:2026-06-01 #ctx:work #proj:alpha #rep:1w #start:2026-05-01", 0)
        assert t.priority == "high"
        assert t.due == "2026-06-01"
        assert t.context == "work"
        assert t.project == "alpha"
        assert t.recurrence == "1w"
        assert t.start == "2026-05-01"

    def test_star_tag_no_value(self):
        t = Task("- [ ] Important #star", 0)
        assert "#star" in t.tags
        assert t.tags["#star"] is None

    def test_no_description_only_tags(self):
        t = Task("- [ ] #p:high #due:2026-01-01", 0)
        assert t.description == ""
        assert t.priority == "high"

    def test_indented_task(self):
        t = Task("  - [ ] Nested task", 0)
        assert t.indent == "  "
        assert t.description == "Nested task"

    def test_invalid_line_returns_empty(self):
        t = Task("not a task line", 0)
        assert t.description == ""
        assert t.complete is False
        assert t.tags == {}

    def test_double_colon_corruption_stripped(self):
        t = Task("- [ ] Task #p::high", 0)
        assert t.priority == "high"

    def test_description_with_hash_in_text(self):
        t = Task("- [ ] Fix issue #123 in code #p:low", 0)
        assert t.priority == "low"

    def test_notes_stored(self):
        t = Task("- [ ] Task", 0, raw_notes="  a note\n  another")
        assert "a note" in t.notes
        assert "another" in t.notes

    def test_section_defaults_none(self):
        t = Task("- [ ] Task", 0)
        assert t.section is None


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_open_task(self):
        line = "- [ ] Buy milk #due:2026-06-01 #p:low"
        t = Task(line, 0)
        assert t.to_line() == line

    def test_complete_task(self):
        line = "- [x] Done #done:2026-05-01 #p:high"
        t = Task(line, 0)
        assert t.to_line() == line

    def test_tags_sorted_alphabetically(self):
        t = Task("- [ ] Task #p:high #due:2026-01-01 #ctx:work", 0)
        result = t.to_line()
        assert result.index("#ctx") < result.index("#due") < result.index("#p")

    def test_star_tag_round_trip(self):
        line = "- [ ] Task #star"
        t = Task(line, 0)
        assert "#star" in t.to_line()

    def test_indented_round_trip(self):
        line = "  - [ ] Nested #p:medium"
        t = Task(line, 0)
        assert t.to_line() == line


# ---------------------------------------------------------------------------
# Setters
# ---------------------------------------------------------------------------

class TestSetters:
    def test_set_due(self):
        t = Task("- [ ] Task", 0)
        t.set_due("2026-12-31")
        assert t.due == "2026-12-31"

    def test_clear_due(self):
        t = Task("- [ ] Task #due:2026-01-01", 0)
        t.set_due(None)
        assert t.due is None
        assert "#due" not in t.tags

    def test_set_priority(self):
        t = Task("- [ ] Task", 0)
        t.set_priority("top")
        assert t.priority == "top"

    def test_clear_priority(self):
        t = Task("- [ ] Task #p:high", 0)
        t.set_priority(None)
        assert t.priority is None

    def test_complete_task_sets_done_date(self):
        t = Task("- [ ] Task", 0)
        today = date.today().isoformat()
        t.complete_task()
        assert t.complete is True
        assert t.tags.get("#done") == today

    def test_reopen_task_clears_done(self):
        t = Task("- [x] Task #done:2026-05-01", 0)
        t.reopen_task()
        assert t.complete is False
        assert "#done" not in t.tags

    def test_set_notes(self):
        t = Task("- [ ] Task", 0)
        t.set_notes("  a note")
        assert t.raw_notes == "  a note"


# ---------------------------------------------------------------------------
# Recurrence
# ---------------------------------------------------------------------------

class TestRecurrence:
    def _completed_task(self, due: str, rep: str) -> Task:
        t = Task(f"- [ ] Task #due:{due} #rep:{rep}", 0)
        t.complete_task()
        return t

    def test_no_recurrence_returns_none(self):
        t = Task("- [x] Task #done:2026-05-01", 0)
        assert t.get_next_recurrence() is None

    def test_incomplete_task_returns_none(self):
        t = Task("- [ ] Task #rep:1w", 0)
        assert t.get_next_recurrence() is None

    def test_daily_recurrence(self):
        t = self._completed_task("2026-06-01", "1d")
        nxt = t.get_next_recurrence()
        assert nxt is not None
        assert nxt.due == "2026-06-02"
        assert nxt.complete is False

    def test_weekly_recurrence(self):
        t = self._completed_task("2026-06-01", "1w")
        nxt = t.get_next_recurrence()
        assert nxt.due == "2026-06-08"

    def test_two_week_recurrence(self):
        t = self._completed_task("2026-06-01", "2w")
        nxt = t.get_next_recurrence()
        assert nxt.due == "2026-06-15"

    def test_monthly_recurrence(self):
        t = self._completed_task("2026-06-15", "1m")
        nxt = t.get_next_recurrence()
        assert nxt.due == "2026-07-15"

    def test_monthly_end_of_month(self):
        t = self._completed_task("2026-01-31", "1m")
        nxt = t.get_next_recurrence()
        assert nxt.due == "2026-02-28"

    def test_monthly_end_of_month_leap_year(self):
        t = self._completed_task("2024-01-31", "1m")
        nxt = t.get_next_recurrence()
        assert nxt.due == "2024-02-29"

    def test_monthly_crosses_year(self):
        t = self._completed_task("2026-11-15", "2m")
        nxt = t.get_next_recurrence()
        assert nxt.due == "2027-01-15"

    def test_yearly_recurrence(self):
        t = self._completed_task("2026-03-10", "1y")
        nxt = t.get_next_recurrence()
        assert nxt.due == "2027-03-10"

    def test_relative_recurrence_uses_today(self):
        t = self._completed_task("2026-01-01", "7d+")
        today = date.today()
        nxt = t.get_next_recurrence()
        expected = (today + timedelta(days=7)).isoformat()
        assert nxt.due == expected

    def test_fixed_recurrence_uses_due_date(self):
        t = self._completed_task("2026-06-01", "7d")
        nxt = t.get_next_recurrence()
        assert nxt.due == "2026-06-08"

    def test_recurrence_clears_done_tag(self):
        t = self._completed_task("2026-06-01", "1w")
        nxt = t.get_next_recurrence()
        assert "#done" not in nxt.tags

    def test_recurrence_preserves_description(self):
        t = self._completed_task("2026-06-01", "1w")
        t.description = "Weekly review"
        nxt = t.get_next_recurrence()
        assert nxt.description == "Weekly review"

    def test_recurrence_preserves_notes(self):
        t = self._completed_task("2026-06-01", "1w")
        t.raw_notes = "  check email first"
        nxt = t.get_next_recurrence()
        assert nxt.raw_notes == "  check email first"

    def test_invalid_recurrence_pattern_returns_none(self):
        t = Task("- [x] Task #rep:invalid #done:2026-05-01", 0)
        assert t.get_next_recurrence() is None


# ---------------------------------------------------------------------------
# read_tasks / write_tasks  (all use explicit tasks_file path)
# ---------------------------------------------------------------------------

class TestReadWriteTasks:
    def test_read_tasks_empty_file(self, tmp_tasks_file):
        tmp_tasks_file.write_text("", encoding="utf-8")
        tasks = read_tasks(tmp_tasks_file)
        assert tasks == []

    def test_read_tasks_missing_file(self, tmp_path):
        nonexistent = tmp_path / "nonexistent.md"
        assert read_tasks(nonexistent) == []

    def test_read_tasks_count(self, tmp_tasks_file):
        tasks = read_tasks(tmp_tasks_file)
        assert len(tasks) == 4

    def test_read_tasks_sections(self, tmp_tasks_file):
        tasks = read_tasks(tmp_tasks_file)
        sections = [t.section for t in tasks]
        assert "Inbox" in sections
        assert "Work" in sections

    def test_read_tasks_preserves_notes(self, tmp_tasks_file):
        tmp_tasks_file.write_text(
            "# Tasks\n\n## Inbox\n\n- [ ] Task with note\n  this is the note\n",
            encoding="utf-8",
        )
        tasks = read_tasks(tmp_tasks_file)
        assert len(tasks) == 1
        assert "this is the note" in tasks[0].notes

    def test_write_tasks_round_trip(self, tmp_tasks_file):
        original = read_tasks(tmp_tasks_file)
        write_tasks(original, tmp_tasks_file)
        reloaded = read_tasks(tmp_tasks_file)
        assert len(reloaded) == len(original)
        for a, b in zip(original, reloaded):
            assert a.description == b.description
            assert a.complete == b.complete
            assert a.tags == b.tags

    def test_write_tasks_modifies_field(self, tmp_tasks_file):
        tasks = read_tasks(tmp_tasks_file)
        tasks[0].set_priority("top")
        write_tasks(tasks, tmp_tasks_file)
        reloaded = read_tasks(tmp_tasks_file)
        assert reloaded[0].priority == "top"

    def test_write_tasks_complete(self, tmp_tasks_file):
        tasks = read_tasks(tmp_tasks_file)
        tasks[0].complete_task()
        write_tasks(tasks, tmp_tasks_file)
        reloaded = read_tasks(tmp_tasks_file)
        assert reloaded[0].complete is True

    def test_write_tasks_notes_preserved(self, tmp_tasks_file):
        tmp_tasks_file.write_text(
            "# Tasks\n\n## Inbox\n\n- [ ] Task\n  my note\n",
            encoding="utf-8",
        )
        tasks = read_tasks(tmp_tasks_file)
        tasks[0].set_priority("high")
        write_tasks(tasks, tmp_tasks_file)
        reloaded = read_tasks(tmp_tasks_file)
        assert "my note" in reloaded[0].notes

    def test_line_num_is_index(self, tmp_tasks_file):
        tasks = read_tasks(tmp_tasks_file)
        for i, t in enumerate(tasks):
            assert t.line_num == i
