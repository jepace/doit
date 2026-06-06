"""Shared fixtures for the doit test suite."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

STOCK_TASKS = """\
# Tasks

## Inbox

- [ ] Buy milk #due:2026-06-01 #id:aa0001 #p:low
- [ ] Call dentist #ctx:phone #id:aa0002 #p:high
- [x] Pay bills #done:2026-05-01 #id:aa0003

## Work

- [ ] Write report #due:2026-05-30 #id:aa0004 #p:high #proj:acme #rep:1w
"""

TEST_EMAIL    = "test@example.com"
TEST_PASSWORD = "testpass1"


def _make_verified_user(tmp_data_dir, email=TEST_EMAIL, password=TEST_PASSWORD, admin=False):
    """Create a verified user directly in tmp_data_dir. Returns user dict."""
    import user_store as us

    user = us.UserStore.create_user(email, password)
    # mark verified
    profile_path = us._profile_path(user["id"])
    profile = us._read_json(profile_path)
    profile["verified"] = True
    profile["admin"] = admin
    us._write_json(profile_path, profile)
    # write stock tasks
    tasks_file = us._user_dir(user["id"]) / "tasks.md"
    tasks_file.write_text(STOCK_TASKS, encoding="utf-8")
    return us.UserStore.get_user(user["id"])


@pytest.fixture()
def tmp_tasks_file(tmp_path, monkeypatch):
    """
    Patch task_manager.DATA_DIR to a temp dir and return a tasks Path
    for a fake user_id 'testuser'.
    """
    import task_manager

    data_dir = tmp_path / "data"
    user_dir = data_dir / "testuser"
    user_dir.mkdir(parents=True)
    tasks_file = user_dir / "tasks.md"
    tasks_file.write_text(STOCK_TASKS, encoding="utf-8")

    monkeypatch.setattr(task_manager, "DATA_DIR", data_dir)
    return tasks_file


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    Flask test client with DATA_DIR patched to a temp dir.
    A verified user (TEST_EMAIL / TEST_PASSWORD) is pre-created.
    """
    import user_store as us
    import task_manager
    import serve

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr(us,           "DATA_DIR", data_dir)
    monkeypatch.setattr(task_manager, "DATA_DIR", data_dir)

    _make_verified_user(data_dir)

    serve.app.config["TESTING"]    = True
    serve.app.config["SECRET_KEY"] = "test-secret"
    serve.app.secret_key           = "test-secret"

    with serve.app.test_client() as c:
        yield c


def get_csrf(client):
    """Fetch a CSRF token from the login page and return it."""
    r = client.get("/auth/login")
    assert r.status_code == 200
    with client.session_transaction() as sess:
        return sess.get("csrf_token", "")


@pytest.fixture()
def authed_client(client):
    """Flask test client already logged in as TEST_EMAIL.

    JSON POSTs automatically receive the X-CSRF-Token header so tests don't
    have to include it manually.
    """
    import user_store as us

    user = us.UserStore.get_by_email(TEST_EMAIL)
    with client.session_transaction() as sess:
        sess["user_id"]    = user["id"]
        sess["csrf_token"] = "testcsrf"
    return _AutoCsrfClient(client, "testcsrf")


class _AutoCsrfClient:
    """Wraps a Flask test client and injects X-CSRF-Token for JSON requests."""

    def __init__(self, client, token: str):
        self._c = client
        self._token = token

    def post(self, *args, **kwargs):
        if kwargs.get("content_type") == "application/json":
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-CSRF-Token", self._token)
            kwargs["headers"] = headers
        return self._c.post(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._c, name)
