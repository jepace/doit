"""Integration tests for serve.py Flask routes."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import TEST_EMAIL, TEST_PASSWORD, get_csrf


# ---------------------------------------------------------------------------
# Auth: register / login / logout
# ---------------------------------------------------------------------------

class TestAuth:
    def test_login_page_get(self, client):
        r = client.get("/auth/login")
        assert r.status_code == 200

    def test_login_success_redirects_to_tasks(self, client):
        csrf = get_csrf(client)
        r = client.post("/auth/login", data={"email": TEST_EMAIL, "password": TEST_PASSWORD, "_csrf_token": csrf})
        assert r.status_code == 302
        assert "/tasks" in r.headers["Location"]

    def test_login_wrong_password(self, client):
        csrf = get_csrf(client)
        r = client.post("/auth/login", data={"email": TEST_EMAIL, "password": "wrongpass", "_csrf_token": csrf})
        assert r.status_code == 200

    def test_login_unknown_email(self, client):
        csrf = get_csrf(client)
        r = client.post("/auth/login", data={"email": "nobody@example.com", "password": "whatever", "_csrf_token": csrf})
        assert r.status_code == 200

    def test_logout_clears_session(self, authed_client):
        with authed_client.session_transaction() as sess:
            csrf = sess["csrf_token"]
        r = authed_client.post("/auth/logout", data={"_csrf_token": csrf})
        assert r.status_code == 302
        r2 = authed_client.get("/tasks")
        assert "/auth/login" in r2.headers["Location"]

    def test_unauthenticated_tasks_redirects_to_login(self, client):
        r = client.get("/tasks")
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_index_redirects(self, authed_client):
        r = authed_client.get("/")
        assert r.status_code == 302

    def test_register_page_get(self, client):
        r = client.get("/register")
        assert r.status_code == 200

    def test_register_duplicate_email(self, client):
        csrf = get_csrf(client)
        r = client.post("/register", data={"email": TEST_EMAIL, "password": TEST_PASSWORD, "confirm": TEST_PASSWORD, "_csrf_token": csrf})
        assert r.status_code in (200, 302)


# ---------------------------------------------------------------------------
# Tasks view
# ---------------------------------------------------------------------------

class TestTasksView:
    def test_tasks_page_renders(self, authed_client):
        r = authed_client.get("/tasks")
        assert r.status_code == 200

    def test_tasks_page_shows_task_descriptions(self, authed_client):
        r = authed_client.get("/tasks")
        assert b"Buy milk" in r.data
        assert b"Call dentist" in r.data


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------

class TestToggle:
    def test_toggle_complete(self, authed_client):
        r = authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"line": 0, "action": "complete"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_toggle_reopen(self, authed_client):
        authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"line": 0, "action": "complete"}),
            content_type="application/json",
        )
        r = authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"line": 0, "action": "reopen"}),
            content_type="application/json",
        )
        assert r.get_json()["ok"] is True

    def test_toggle_missing_line_returns_400(self, authed_client):
        r = authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"action": "complete"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_toggle_invalid_action_returns_400(self, authed_client):
        r = authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"line": 0, "action": "destroy"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_toggle_unauthenticated_redirects(self, client):
        r = client.post(
            "/tasks/toggle",
            data=json.dumps({"line": 0, "action": "complete"}),
            content_type="application/json",
        )
        assert r.status_code == 302


# ---------------------------------------------------------------------------
# Add task
# ---------------------------------------------------------------------------

class TestAddTask:
    def test_add_task(self, authed_client):
        r = authed_client.post(
            "/tasks/add",
            data=json.dumps({"text": "New task here"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_add_task_to_section(self, authed_client):
        r = authed_client.post(
            "/tasks/add",
            data=json.dumps({"text": "Work item", "section": "Work"}),
            content_type="application/json",
        )
        assert r.get_json()["ok"] is True

    def test_add_empty_task_returns_400(self, authed_client):
        r = authed_client.post(
            "/tasks/add",
            data=json.dumps({"text": "   "}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_add_task_appears_in_tasks_list(self, authed_client):
        authed_client.post(
            "/tasks/add",
            data=json.dumps({"text": "Unique task XYZ"}),
            content_type="application/json",
        )
        r = authed_client.get("/tasks")
        assert b"Unique task XYZ" in r.data


# ---------------------------------------------------------------------------
# Update task
# ---------------------------------------------------------------------------

class TestUpdateTask:
    def _update(self, client, task_id, field, value):
        return client.post(
            "/tasks/update",
            data=json.dumps({"task_id": task_id, "field": field, "value": value}),
            content_type="application/json",
        )

    def test_update_description(self, authed_client):
        r = self._update(authed_client, 0, "description", "Updated description")
        assert r.get_json()["ok"] is True

    def test_update_priority(self, authed_client):
        r = self._update(authed_client, 0, "priority", "top")
        assert r.get_json()["ok"] is True

    def test_update_due(self, authed_client):
        r = self._update(authed_client, 0, "due", "2027-01-01")
        assert r.get_json()["ok"] is True

    def test_update_context(self, authed_client):
        r = self._update(authed_client, 0, "context", "home")
        assert r.get_json()["ok"] is True

    def test_update_project(self, authed_client):
        r = self._update(authed_client, 0, "project", "myproj")
        assert r.get_json()["ok"] is True

    def test_update_recurrence(self, authed_client):
        r = self._update(authed_client, 0, "recurrence", "2w")
        assert r.get_json()["ok"] is True

    def test_update_start(self, authed_client):
        r = self._update(authed_client, 0, "start", "2026-06-01")
        assert r.get_json()["ok"] is True

    def test_update_notes(self, authed_client):
        r = self._update(authed_client, 0, "notes", "a note here")
        assert r.get_json()["ok"] is True

    def test_complete_task(self, authed_client):
        r = self._update(authed_client, 0, "complete", "true")
        assert r.get_json()["ok"] is True

    def test_reopen_task(self, authed_client):
        self._update(authed_client, 0, "complete", "true")
        r = self._update(authed_client, 0, "complete", "false")
        assert r.get_json()["ok"] is True

    def test_unknown_field_returns_400(self, authed_client):
        r = self._update(authed_client, 0, "badfield", "x")
        assert r.status_code == 400

    def test_missing_task_id_returns_400(self, authed_client):
        r = authed_client.post(
            "/tasks/update",
            data=json.dumps({"field": "priority", "value": "high"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_out_of_range_task_id_returns_404(self, authed_client):
        r = self._update(authed_client, 999, "priority", "high")
        assert r.status_code == 404

    def test_complete_recurring_task_returns_next_task(self, authed_client):
        # Task index 3 is "Write report #rep:1w"
        r = self._update(authed_client, 3, "complete", "true")
        data = r.get_json()
        assert data["ok"] is True
        assert "next_task" in data
        assert data["next_task"]["recurrence"] == "1w"


# ---------------------------------------------------------------------------
# Bulk update
# ---------------------------------------------------------------------------

class TestBulkUpdate:
    def _bulk(self, client, action, task_ids, value=""):
        return client.post(
            "/tasks/bulk-update",
            data=json.dumps({"action": action, "task_ids": task_ids, "value": value}),
            content_type="application/json",
        )

    def test_bulk_set_priority(self, authed_client):
        r = self._bulk(authed_client, "set-priority", [0, 1], "high")
        assert r.get_json()["ok"] is True

    def test_bulk_set_context(self, authed_client):
        r = self._bulk(authed_client, "set-context", [0], "home")
        assert r.get_json()["ok"] is True

    def test_bulk_set_due(self, authed_client):
        r = self._bulk(authed_client, "set-due", [0, 1], "2027-01-01")
        assert r.get_json()["ok"] is True

    def test_bulk_set_project(self, authed_client):
        r = self._bulk(authed_client, "set-project", [0], "bigproject")
        assert r.get_json()["ok"] is True

    def test_bulk_delete(self, authed_client):
        r = self._bulk(authed_client, "delete", [0])
        assert r.get_json()["ok"] is True

    def test_bulk_out_of_range_skipped(self, authed_client):
        r = self._bulk(authed_client, "set-priority", [999], "high")
        assert r.get_json()["ok"] is True

    def test_bulk_missing_action_returns_400(self, authed_client):
        r = authed_client.post(
            "/tasks/bulk-update",
            data=json.dumps({"task_ids": [0]}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_bulk_unknown_action_returns_400(self, authed_client):
        r = self._bulk(authed_client, "explode", [0])
        assert r.status_code == 400

    def test_bulk_unauthenticated_redirects(self, client):
        r = self._bulk(client, "set-priority", [0], "high")
        assert r.status_code == 302
