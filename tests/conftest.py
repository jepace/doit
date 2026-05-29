"""
Shared fixtures for the doit test suite.
"""

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

TEST_EMAIL = "test@example.com"
TEST_PASSWORD = "testpass1"


@pytest.fixture()
def tmp_tasks_file(tmp_path):
    """
    Write stock tasks.md into a temp dir and return the Path.
    Pass this path explicitly to read_tasks(tasks_file) and write_tasks(tasks, tasks_file).
    """
    tasks_file = tmp_path / "tasks.md"
    tasks_file.write_text(STOCK_TASKS, encoding="utf-8")
    return tasks_file


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    Flask test client with:
    - a temp data dir patched into user_store.DATA_DIR and task_manager.DATA_DIR
    - a real test user created via UserStore
    - serve.DATA_DIR patched
    """
    import user_store
    import task_manager
    import serve

    # Patch DATA_DIR everywhere
    monkeypatch.setattr(user_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(task_manager, "DATA_DIR", tmp_path)
    monkeypatch.setattr(serve, "DATA_DIR", tmp_path)

    # Create test user
    profile = user_store.UserStore.create_user(TEST_EMAIL, TEST_PASSWORD, admin=True)
    # Mark verified so login works
    profile["verified"] = True
    user_store.UserStore.save_profile(profile)

    # Write stock tasks file for this user
    tasks_file = tmp_path / profile["id"] / "tasks.md"
    tasks_file.write_text(STOCK_TASKS, encoding="utf-8")

    serve.app.config["TESTING"] = True
    serve.app.config["SECRET_KEY"] = "test-secret"
    serve.app.secret_key = "test-secret"

    with serve.app.test_client() as c:
        c._test_user_id = profile["id"]
        yield c


@pytest.fixture()
def authed_client(client):
    """Flask test client already logged in as the test user."""
    with client.session_transaction() as sess:
        sess["user_id"] = client._test_user_id
        sess["csrf_token"] = "test-csrf"
    return client
