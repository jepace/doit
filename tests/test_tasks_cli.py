"""
Tests for the tasks.py CLI filter tool.
"""

import sys
from pathlib import Path
from datetime import date, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import STOCK_TASKS


@pytest.fixture()
def tasks_file(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text(STOCK_TASKS, encoding="utf-8")
    return f


def run_cli(args: list, tasks_file: Path) -> tuple[str, int]:
    """Run main() with patched sys.argv and capture stdout. Returns (output, exit_code)."""
    import io, contextlib
    import tasks as cli

    argv = ["tasks.py", "--file", str(tasks_file)] + args
    output = io.StringIO()
    exit_code = 0
    with contextlib.redirect_stdout(output):
        try:
            old_argv = sys.argv
            sys.argv = argv
            cli.main()
        except SystemExit as e:
            exit_code = int(e.code or 0)
        finally:
            sys.argv = old_argv
    return output.getvalue(), exit_code


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

class TestBasicInvocation:
    def test_shows_open_tasks_by_default(self, tasks_file):
        out, code = run_cli([], tasks_file)
        assert code == 0
        assert "Buy milk" in out
        assert "Call dentist" in out

    def test_does_not_show_completed_by_default(self, tasks_file):
        out, _ = run_cli([], tasks_file)
        assert "Pay bills" not in out

    def test_done_flag_shows_completed_only(self, tasks_file):
        out, code = run_cli(["--done"], tasks_file)
        assert code == 0
        assert "Pay bills" in out
        assert "Buy milk" not in out

    def test_all_flag_shows_everything(self, tasks_file):
        out, code = run_cli(["--all"], tasks_file)
        assert "Buy milk" in out
        assert "Pay bills" in out

    def test_missing_file_arg_exits_nonzero(self, tmp_path):
        import io, contextlib
        import tasks as cli
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            try:
                sys.argv = ["tasks.py"]
                cli.main()
                assert False, "should have exited"
            except SystemExit as e:
                assert int(e.code or 0) != 0
            finally:
                sys.argv = sys.argv[:1]

    def test_nonexistent_file_exits_nonzero(self, tmp_path):
        import io, contextlib
        import tasks as cli
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                sys.argv = ["tasks.py", "--file", str(tmp_path / "nope.md")]
                cli.main()
                assert False
            except SystemExit as e:
                assert int(e.code or 0) != 0
            finally:
                sys.argv = sys.argv[:1]

    def test_help_flag_exits_zero(self, tasks_file):
        import io, contextlib
        import tasks as cli
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                sys.argv = ["tasks.py", "--help"]
                cli.main()
            except SystemExit as e:
                assert int(e.code or 0) == 0
            finally:
                sys.argv = sys.argv[:1]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

class TestFilters:
    def test_filter_by_priority(self, tasks_file):
        out, _ = run_cli(["--priority", "high"], tasks_file)
        assert "Call dentist" in out
        assert "Buy milk" not in out   # low priority

    def test_filter_by_context(self, tasks_file):
        out, _ = run_cli(["--context", "phone"], tasks_file)
        assert "Call dentist" in out
        assert "Buy milk" not in out

    def test_filter_by_project(self, tasks_file):
        out, _ = run_cli(["--project", "acme"], tasks_file)
        assert "Write report" in out
        assert "Buy milk" not in out

    def test_repeat_filter(self, tasks_file):
        out, _ = run_cli(["--repeat"], tasks_file)
        assert "Write report" in out
        assert "Buy milk" not in out

    def test_due_today_filter(self, tasks_file):
        # Write report has due:2026-05-30 which is "today" in test context.
        # Buy milk has 2026-06-01 (future). Use a custom file for determinism.
        from task_manager import DATA_DIR
        today = date.today().isoformat()
        content = (
            "# Tasks\n\n## Inbox\n\n"
            f"- [ ] Due today task #due:{today}\n"
            f"- [ ] Future task #due:{(date.today() + timedelta(days=5)).isoformat()}\n"
        )
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as tf:
            tf.write(content)
            tf.flush()
            out, _ = run_cli(["--due-today", "--file", tf.name], Path(tf.name))
            # run_cli injects --file already via tasks_file param, but here we pass path directly
        # Parse manually to avoid fixture collision
        import io, contextlib
        import tasks as cli
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                sys.argv = ["tasks.py", "--file", tf.name, "--due-today"]
                cli.main()
            except SystemExit:
                pass
            finally:
                sys.argv = sys.argv[:1]
        out = output.getvalue()
        assert "Due today task" in out
        assert "Future task" not in out

    def test_no_match_exits_zero_with_message(self, tasks_file):
        out, code = run_cli(["--context", "nonexistent_ctx_xyz"], tasks_file)
        assert code == 0
        assert "No tasks" in out

    def test_unknown_option_exits_nonzero(self, tasks_file):
        import io, contextlib
        import tasks as cli
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                sys.argv = ["tasks.py", "--file", str(tasks_file), "--notanoption"]
                cli.main()
                assert False
            except SystemExit as e:
                assert int(e.code or 0) != 0
            finally:
                sys.argv = sys.argv[:1]


# ---------------------------------------------------------------------------
# --user flag (H5)
# ---------------------------------------------------------------------------

class TestUserFlag:
    def test_user_flag_reads_correct_file(self, tmp_path, monkeypatch):
        import task_manager
        import tasks as cli

        data_dir = tmp_path / "data"
        user_dir = data_dir / "abc123"
        user_dir.mkdir(parents=True)
        (user_dir / "tasks.md").write_text(
            "# Tasks\n\n## Inbox\n\n- [ ] User specific task\n", encoding="utf-8"
        )
        monkeypatch.setattr(task_manager, "DATA_DIR", data_dir)

        import io, contextlib
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                sys.argv = ["tasks.py", "--user", "abc123"]
                cli.main()
            except SystemExit:
                pass
            finally:
                sys.argv = sys.argv[:1]

        assert "User specific task" in output.getvalue()
