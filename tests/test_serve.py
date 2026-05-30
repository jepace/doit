"""Integration tests for serve.py Flask routes."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import TEST_EMAIL, TEST_PASSWORD, get_csrf
from conftest import STOCK_TASKS


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

    def test_bulk_delete_removes_task_from_file(self, authed_client):
        """M3: deleted tasks must be absent from the file, not written as [DELETED]."""
        import user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        tasks_file = us._user_dir(user["id"]) / "tasks.md"

        from task_manager import read_tasks
        before = read_tasks(tasks_file)
        count_before = len(before)

        r = self._bulk(authed_client, "delete", [0])
        assert r.get_json()["ok"] is True

        after = read_tasks(tasks_file)
        assert len(after) == count_before - 1
        assert all("[DELETED]" not in t.description for t in after)


# ---------------------------------------------------------------------------
# Recurrence — atomic write (M1)
# ---------------------------------------------------------------------------

class TestRecurrenceAtomicWrite:
    def test_completing_recurring_task_writes_next_in_one_pass(self, authed_client):
        """M1: recurrence task must appear in the file after a single write."""
        import user_store as us
        from task_manager import read_tasks

        user = us.UserStore.get_by_email(TEST_EMAIL)
        tasks_file = us._user_dir(user["id"]) / "tasks.md"

        # Write report (#rep:1w) is task index 3 in STOCK_TASKS
        tasks = read_tasks(tasks_file)
        recurring_idx = next(i for i, t in enumerate(tasks) if t.recurrence)

        r = authed_client.post(
            "/tasks/update",
            data=json.dumps({"task_id": recurring_idx, "field": "complete", "value": "true"}),
            content_type="application/json",
        )
        assert r.get_json()["ok"] is True

        after = read_tasks(tasks_file)
        # There should now be one more task (the next recurrence)
        assert len(after) == len(tasks) + 1
        new_task = after[-1]
        assert new_task.recurrence == "1w"
        assert not new_task.complete


# ---------------------------------------------------------------------------
# Security — CSRF enforcement on JSON endpoints (C1)
# ---------------------------------------------------------------------------

class TestCsrfEnforcement:
    def test_json_post_without_csrf_header_returns_403(self, client):
        """Authenticated JSON POST without X-CSRF-Token must be rejected."""
        import user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        with client.session_transaction() as sess:
            sess["user_id"]    = user["id"]
            sess["csrf_token"] = "secret"
        # Post without the header
        r = client.post(
            "/tasks/add",
            data=json.dumps({"text": "sneak"}),
            content_type="application/json",
        )
        assert r.status_code == 403

    def test_json_post_with_wrong_csrf_header_returns_403(self, client):
        import user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        with client.session_transaction() as sess:
            sess["user_id"]    = user["id"]
            sess["csrf_token"] = "secret"
        r = client.post(
            "/tasks/add",
            data=json.dumps({"text": "sneak"}),
            content_type="application/json",
            headers={"X-CSRF-Token": "wrong"},
        )
        assert r.status_code == 403

    def test_json_post_with_correct_csrf_header_succeeds(self, authed_client):
        r = authed_client.post(
            "/tasks/add",
            data=json.dumps({"text": "legit task"}),
            content_type="application/json",
        )
        assert r.get_json()["ok"] is True


# ---------------------------------------------------------------------------
# Security — _ip() and _safe_next (H2, L3)
# ---------------------------------------------------------------------------

class TestIpAndSafeNext:
    def test_ip_ignores_x_forwarded_for_without_proxy(self, client):
        """H2: X-Forwarded-For must not be trusted when ProxyFix is inactive."""
        import serve
        with serve.app.test_request_context(
            "/",
            environ_base={"REMOTE_ADDR": "1.2.3.4"},
            headers={"X-Forwarded-For": "9.9.9.9"},
        ):
            assert serve._ip() == "1.2.3.4"

    def test_safe_next_rejects_protocol_relative(self):
        """L3: //evil.com must be rejected."""
        import serve
        with serve.app.test_request_context("/"):
            assert serve._safe_next("//evil.com/steal") == "/tasks"

    def test_safe_next_rejects_backslash_protocol_relative(self):
        import serve
        with serve.app.test_request_context("/"):
            assert serve._safe_next("/\\evil.com") == "/tasks"

    def test_safe_next_rejects_absolute_url(self):
        import serve
        with serve.app.test_request_context("/"):
            assert serve._safe_next("https://evil.com") == "/tasks"

    def test_safe_next_allows_relative_path(self):
        import serve
        with serve.app.test_request_context("/"):
            assert serve._safe_next("/tasks") == "/tasks"


# ---------------------------------------------------------------------------
# Auth — unverified login does NOT auto-resend email (H3b)
# ---------------------------------------------------------------------------

class TestUnverifiedLogin:
    def test_unverified_login_redirects_to_verify_pending(self, client, monkeypatch):
        """H3b: login with unverified account must redirect without sending email."""
        import user_store as us
        import serve

        sent = []
        monkeypatch.setattr(serve, "send_verification_email", lambda *a, **kw: sent.append(a) or True)

        # Create unverified user
        us.UserStore.create_user("unverif@example.com", TEST_PASSWORD)
        csrf = get_csrf(client)
        r = client.post("/auth/login", data={
            "email": "unverif@example.com",
            "password": TEST_PASSWORD,
            "_csrf_token": csrf,
        })
        assert r.status_code == 302
        assert "verify" in r.headers["Location"]
        assert sent == [], "No verification email should be sent automatically on login"


# ---------------------------------------------------------------------------
# Resend-verify — rate limiting (M4)
# ---------------------------------------------------------------------------

class TestResendVerifyRateLimit:
    def test_resend_verify_rate_limited_by_ip(self, client, monkeypatch):
        """M4: resend-verify must be rate-limited."""
        import serve
        monkeypatch.setattr(serve, "send_verification_email", lambda *a, **kw: True)

        import user_store as us
        us.UserStore.create_user("rl@example.com", TEST_PASSWORD)

        # Exhaust the 3/hour limit
        for _ in range(3):
            csrf = get_csrf(client)
            client.post("/auth/resend-verify", data={"email": "rl@example.com", "_csrf_token": csrf})

        sent_after_limit = []
        monkeypatch.setattr(serve, "send_verification_email",
                            lambda *a, **kw: sent_after_limit.append(a) or True)

        csrf = get_csrf(client)
        client.post("/auth/resend-verify", data={"email": "rl@example.com", "_csrf_token": csrf})
        assert sent_after_limit == [], "Email must not be sent once rate limit is hit"


# ---------------------------------------------------------------------------
# Anonymous sessions — no cookie created for GET requests (L6)
# ---------------------------------------------------------------------------

class TestNoAnonymousSession:
    def test_anon_get_login_does_not_set_session_cookie(self, client):
        """L6: anonymous GET must not create a session / set a cookie."""
        r = client.get("/auth/login")
        assert r.status_code == 200
        assert "Set-Cookie" not in r.headers


# ---------------------------------------------------------------------------
# Bulk update optimistic concurrency
# ---------------------------------------------------------------------------

class TestBulkConcurrency:
    def _get_hash(self, authed_client, task_id):
        from task_manager import read_tasks
        import user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        tasks_file = us._user_dir(user["id"]) / "tasks.md"
        tasks = read_tasks(tasks_file)
        return tasks[task_id].content_hash

    def _bulk_with_hashes(self, client, action, task_ids, hashes, value=""):
        return client.post(
            "/tasks/bulk-update",
            data=json.dumps({"action": action, "task_ids": task_ids,
                             "value": value, "task_hashes": hashes}),
            content_type="application/json",
        )

    def test_bulk_correct_hash_succeeds(self, authed_client):
        h = self._get_hash(authed_client, 0)
        r = self._bulk_with_hashes(authed_client, "set-priority", [0], {"0": h}, "high")
        assert r.get_json()["ok"] is True

    def test_bulk_wrong_hash_returns_409(self, authed_client):
        r = self._bulk_with_hashes(authed_client, "set-priority", [0], {"0": "deadbeef"}, "high")
        assert r.status_code == 409

    def test_bulk_no_hashes_still_works(self, authed_client):
        r = self._bulk_with_hashes(authed_client, "set-priority", [0], {}, "high")
        assert r.get_json()["ok"] is True
