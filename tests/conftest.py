"""
Shared fixtures for the doit test suite.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

# Ensure tools/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

STOCK_TASKS = """\
# Tasks

## Inbox

- [ ] Buy milk #p:low #due:2026-06-01
- [ ] Call dentist #p:high #ctx:phone
- [x] Pay bills #done:2026-05-01

## Work

- [ ] Write report #p:high #proj:acme #due:2026-05-30 #rep:1w
"""


def _sha256(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


@pytest.fixture()
def tmp_tasks_file(tmp_path, monkeypatch):
    """
    Write stock tasks.md into a temp dir, patch task_manager.TASKS_FILE,
    and return the Path so tests can inspect / overwrite it.
    """
    import task_manager

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    tasks_file = wiki / "tasks.md"
    tasks_file.write_text(STOCK_TASKS, encoding="utf-8")

    monkeypatch.setattr(task_manager, "TASKS_FILE", tasks_file)
    return tasks_file


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    Flask test client with:
    - a temp wiki dir (tasks.md + .passwd)
    - WIKI_DIR and PASSWD_FILE patched in serve
    - task_manager.TASKS_FILE patched to the same file
    - a valid password hash written to .passwd
    """
    import task_manager
    import serve

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    tasks_file = wiki / "tasks.md"
    tasks_file.write_text(STOCK_TASKS, encoding="utf-8")

    passwd_file = wiki / ".passwd"
    passwd_file.write_text(_sha256("testpass1"))

    monkeypatch.setattr(serve, "WIKI_DIR", wiki)
    monkeypatch.setattr(serve, "PASSWD_FILE", passwd_file)
    monkeypatch.setattr(task_manager, "TASKS_FILE", tasks_file)

    serve.app.config["TESTING"] = True
    serve.app.config["SECRET_KEY"] = "test-secret"
    serve.app.secret_key = "test-secret"

    with serve.app.test_client() as c:
        yield c


@pytest.fixture()
def authed_client(client):
    """Flask test client already logged in."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client
