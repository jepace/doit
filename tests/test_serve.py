"""Integration tests for serve.py Flask routes."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import TEST_EMAIL, TEST_PASSWORD, get_csrf
from conftest import STOCK_TASKS


def _task_ids(authed_client):
    """Return list of task IDs (stable #id strings) for the current user's tasks."""
    import user_store as us
    from task_manager import read_tasks
    user = us.UserStore.get_by_email(TEST_EMAIL)
    tasks_file = us._user_dir(user["id"]) / "tasks.md"
    tasks = read_tasks(tasks_file)
    return [t.id for t in tasks]


def _task_id(authed_client, index: int) -> str:
    """Return the stable #id of the task at the given index."""
    return _task_ids(authed_client)[index]


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
        assert r.headers["Location"].endswith("/")

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
        r2 = authed_client.get("/")
        assert "/auth/login" in r2.headers["Location"]

    def test_unauthenticated_tasks_redirects_to_login(self, client):
        r = client.get("/")
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_index_serves_tasks(self, authed_client):
        r = authed_client.get("/")
        assert r.status_code == 200

    def test_tasks_legacy_redirects_to_root(self, client):
        r = client.get("/tasks")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/")

    def test_register_page_get(self, client):
        r = client.get("/register")
        assert r.status_code == 200

    def test_register_duplicate_email(self, client):
        csrf = get_csrf(client)
        r = client.post("/register", data={"email": TEST_EMAIL, "password": TEST_PASSWORD, "confirm": TEST_PASSWORD, "_csrf_token": csrf})
        assert r.status_code in (200, 302)

    def test_register_honeypot_silently_rejects(self, client):
        import user_store as us
        csrf = get_csrf(client)
        r = client.post("/register", data={
            "email": "bot@example.com", "password": "botpassword1",
            "confirm": "botpassword1", "website": "http://spam.example",
            "_csrf_token": csrf,
        })
        # Looks like success to the caller...
        assert r.status_code == 302
        # ...but no account was actually created.
        assert us.UserStore.get_by_email("bot@example.com") is None

    def test_register_without_honeypot_still_works(self, client):
        import user_store as us
        csrf = get_csrf(client)
        r = client.post("/register", data={
            "email": "human@example.com", "password": "humanpassword1",
            "confirm": "humanpassword1", "website": "",
            "_csrf_token": csrf,
        })
        assert r.status_code == 302
        assert us.UserStore.get_by_email("human@example.com") is not None


# ---------------------------------------------------------------------------
# Two-factor auth
# ---------------------------------------------------------------------------

class TestTwoFactorAuth:
    def test_2fa_setup_page_renders(self, authed_client):
        r = authed_client.get("/auth/2fa/setup")
        assert r.status_code == 200
        assert b"QR" in r.data or b"2FA" in r.data

    def test_2fa_enable_then_login_requires_code(self, client):
        import pyotp, user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        secret = pyotp.random_base32()
        us.UserStore.enable_totp(user["id"], secret)
        try:
            csrf = get_csrf(client)
            r = client.post("/auth/login",
                            data={"email": TEST_EMAIL, "password": TEST_PASSWORD,
                                  "_csrf_token": csrf})
            assert r.status_code == 302
            assert "/auth/2fa" in r.headers["Location"]
        finally:
            us.UserStore.disable_totp(user["id"])

    def test_2fa_valid_code_completes_login(self, client):
        import pyotp, user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        secret = pyotp.random_base32()
        us.UserStore.enable_totp(user["id"], secret)
        try:
            csrf = get_csrf(client)
            client.post("/auth/login",
                        data={"email": TEST_EMAIL, "password": TEST_PASSWORD,
                              "_csrf_token": csrf})
            code = pyotp.TOTP(secret).now()
            r = client.post("/auth/2fa", data={"code": code})
            assert r.status_code == 302
            assert r.headers["Location"].endswith("/")
        finally:
            us.UserStore.disable_totp(user["id"])

    def test_2fa_invalid_code_rejected(self, client):
        import pyotp, user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        secret = pyotp.random_base32()
        us.UserStore.enable_totp(user["id"], secret)
        try:
            csrf = get_csrf(client)
            client.post("/auth/login",
                        data={"email": TEST_EMAIL, "password": TEST_PASSWORD,
                              "_csrf_token": csrf})
            r = client.post("/auth/2fa", data={"code": "000000"})
            assert r.status_code == 200
            assert b"Invalid" in r.data
        finally:
            us.UserStore.disable_totp(user["id"])

    def test_2fa_page_without_pending_redirects(self, client):
        r = client.get("/auth/2fa")
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_2fa_disable(self, authed_client):
        import pyotp, user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        us.UserStore.enable_totp(user["id"], pyotp.random_base32())
        with authed_client.session_transaction() as sess:
            csrf = sess["csrf_token"]
        r = authed_client.post("/auth/2fa/disable", data={"_csrf_token": csrf})
        assert r.status_code == 302
        user = us.UserStore.get_by_email(TEST_EMAIL)
        assert not user.get("totp_secret")


# ---------------------------------------------------------------------------
# Tasks view
# ---------------------------------------------------------------------------

class TestTasksView:
    def test_tasks_page_renders(self, authed_client):
        r = authed_client.get("/")
        assert r.status_code == 200

    def test_tasks_page_shows_task_descriptions(self, authed_client):
        r = authed_client.get("/")
        assert b"Buy milk" in r.data
        assert b"Call dentist" in r.data


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------

class TestToggle:
    def test_toggle_complete(self, authed_client):
        tid = _task_id(authed_client, 0)
        r = authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"line": tid, "action": "complete"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_toggle_reopen(self, authed_client):
        tid = _task_id(authed_client, 0)
        authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"line": tid, "action": "complete"}),
            content_type="application/json",
        )
        r = authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"line": tid, "action": "reopen"}),
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
        tid = _task_id(authed_client, 0)
        r = authed_client.post(
            "/tasks/toggle",
            data=json.dumps({"line": tid, "action": "destroy"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_toggle_unauthenticated_redirects(self, client):
        r = client.post(
            "/tasks/toggle",
            data=json.dumps({"line": "abc123", "action": "complete"}),
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
        r = authed_client.get("/")
        assert b"Unique task XYZ" in r.data


# ---------------------------------------------------------------------------
# Quick add (Siri Shortcuts) — token-authenticated, no session/CSRF involved
# ---------------------------------------------------------------------------

class TestQuickAdd:
    def _token(self):
        import user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        return us.UserStore.create_api_token(user["id"])

    def test_valid_token_adds_task(self, client):
        token = self._token()
        r = client.post(
            "/api/quick-add",
            data=json.dumps({"text": "Call the vet"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_added_task_is_due_today(self, client):
        from datetime import date
        token = self._token()
        client.post(
            "/api/quick-add",
            data=json.dumps({"text": "Water the plants"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        import user_store as us
        from task_manager import read_tasks
        user = us.UserStore.get_by_email(TEST_EMAIL)
        tasks = read_tasks(us._user_dir(user["id"]) / "tasks.md")
        added = next(t for t in tasks if t.description == "Water the plants")
        assert added.due == date.today().isoformat()
        assert added.start == date.today().isoformat()

    def test_no_token_returns_401(self, client):
        r = client.post(
            "/api/quick-add",
            data=json.dumps({"text": "Should fail"}),
            content_type="application/json",
        )
        assert r.status_code == 401

    def test_bad_token_returns_401(self, client):
        r = client.post(
            "/api/quick-add",
            data=json.dumps({"text": "Should fail"}),
            content_type="application/json",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert r.status_code == 401

    def test_revoked_token_returns_401(self, client):
        import user_store as us
        token = self._token()
        user = us.UserStore.get_by_email(TEST_EMAIL)
        us.UserStore.revoke_api_token(user["id"])
        r = client.post(
            "/api/quick-add",
            data=json.dumps({"text": "Should fail"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    def test_empty_text_returns_400(self, client):
        token = self._token()
        r = client.post(
            "/api/quick-add",
            data=json.dumps({"text": "   "}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    def test_no_session_or_csrf_needed(self, client):
        """The whole point: this must work with zero cookies/session state,
        since Siri Shortcuts can't carry a browser session or CSRF token."""
        token = self._token()
        r = client.post(
            "/api/quick-add",
            data=json.dumps({"text": "No session needed"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200


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
        r = self._update(authed_client, _task_id(authed_client, 0), "description", "Updated description")
        assert r.get_json()["ok"] is True

    def test_update_priority(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "priority", "top")
        assert r.get_json()["ok"] is True

    def test_update_due(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "due", "2027-01-01")
        assert r.get_json()["ok"] is True

    def test_update_context(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "context", "home")
        assert r.get_json()["ok"] is True

    def test_update_project(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "project", "myproj")
        assert r.get_json()["ok"] is True

    def test_update_recurrence(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "recurrence", "2w")
        assert r.get_json()["ok"] is True

    def test_update_start(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "start", "2026-06-01")
        assert r.get_json()["ok"] is True

    def test_update_notes(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "notes", "a note here")
        assert r.get_json()["ok"] is True

    def test_complete_task(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "complete", "true")
        assert r.get_json()["ok"] is True

    def test_reopen_task(self, authed_client):
        tid = _task_id(authed_client, 0)
        self._update(authed_client, tid, "complete", "true")
        r = self._update(authed_client, tid, "complete", "false")
        assert r.get_json()["ok"] is True

    def test_unknown_field_returns_400(self, authed_client):
        r = self._update(authed_client, _task_id(authed_client, 0), "badfield", "x")
        assert r.status_code == 400

    def test_missing_task_id_returns_400(self, authed_client):
        r = authed_client.post(
            "/tasks/update",
            data=json.dumps({"field": "priority", "value": "high"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_out_of_range_task_id_returns_404(self, authed_client):
        r = self._update(authed_client, "nonexistent-id", "priority", "high")
        assert r.status_code == 404

    def test_complete_recurring_task_returns_next_task(self, authed_client):
        import user_store as us
        from task_manager import read_tasks
        user = us.UserStore.get_by_email(TEST_EMAIL)
        tasks_file = us._user_dir(user["id"]) / "tasks.md"
        tasks = read_tasks(tasks_file)
        recurring = next(t for t in tasks if t.recurrence)
        r = self._update(authed_client, recurring.id, "complete", "true")
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
        ids = _task_ids(authed_client)[:2]
        r = self._bulk(authed_client, "set-priority", ids, "high")
        assert r.get_json()["ok"] is True

    def test_bulk_set_context(self, authed_client):
        r = self._bulk(authed_client, "set-context", [_task_id(authed_client, 0)], "home")
        assert r.get_json()["ok"] is True

    def test_bulk_set_due(self, authed_client):
        ids = _task_ids(authed_client)[:2]
        r = self._bulk(authed_client, "set-due", ids, "2027-01-01")
        assert r.get_json()["ok"] is True

    def test_bulk_set_project(self, authed_client):
        r = self._bulk(authed_client, "set-project", [_task_id(authed_client, 0)], "bigproject")
        assert r.get_json()["ok"] is True

    def test_bulk_delete(self, authed_client):
        r = self._bulk(authed_client, "delete", [_task_id(authed_client, 0)])
        assert r.get_json()["ok"] is True

    def test_bulk_out_of_range_skipped(self, authed_client):
        r = self._bulk(authed_client, "set-priority", ["nonexistent-id"], "high")
        assert r.get_json()["ok"] is True

    def test_bulk_missing_action_returns_400(self, authed_client):
        r = authed_client.post(
            "/tasks/bulk-update",
            data=json.dumps({"task_ids": [_task_id(authed_client, 0)]}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_bulk_unknown_action_returns_400(self, authed_client):
        r = self._bulk(authed_client, "explode", [_task_id(authed_client, 0)])
        assert r.status_code == 400

    def test_bulk_unauthenticated_redirects(self, client):
        r = self._bulk(client, "set-priority", ["abc123"], "high")
        assert r.status_code == 302

    def test_bulk_delete_removes_task_from_file(self, authed_client):
        """M3: deleted tasks must be absent from the file, not written as [DELETED]."""
        import user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        tasks_file = us._user_dir(user["id"]) / "tasks.md"

        from task_manager import read_tasks
        before = read_tasks(tasks_file)
        count_before = len(before)
        target_id = before[0].id

        r = self._bulk(authed_client, "delete", [target_id])
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

        tasks = read_tasks(tasks_file)
        recurring_task = next(t for t in tasks if t.recurrence)

        r = authed_client.post(
            "/tasks/update",
            data=json.dumps({"task_id": recurring_task.id, "field": "complete", "value": "true"}),
            content_type="application/json",
        )
        assert r.get_json()["ok"] is True

        after = read_tasks(tasks_file)
        # Completed task moves to Archive; next recurrence added as open task
        # Total count stays the same (one archived, one new open)
        open_recurring = [t for t in after if t.recurrence and not t.complete]
        archived_recurring = [t for t in after if t.recurrence and t.complete and t.section == "Archive"]
        assert len(open_recurring) == 1
        assert open_recurring[0].recurrence == "1w"
        assert len(archived_recurring) == 1


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
# Security — hardening regressions from the remote-attack-surface review
# ---------------------------------------------------------------------------

class TestRegistrationDoesNotEnumerate:
    """POSTing an address that already has an account must be
    indistinguishable from registering a brand-new one."""

    def test_existing_and_new_email_get_identical_responses(self, client, monkeypatch):
        import serve
        sent = []
        monkeypatch.setattr(serve, "send_verification_email",
                            lambda *a, **k: sent.append(("verify", a[0])) or True)
        monkeypatch.setattr(serve, "send_account_exists_email",
                            lambda *a, **k: sent.append(("exists", a[0])) or True)
        csrf = get_csrf(client)

        def post(email):
            serve._rate_limit.clear()
            r = client.post("/register", data={
                "email": email, "password": "somepassword1",
                "confirm": "somepassword1", "_csrf_token": csrf})
            return (r.status_code, r.headers.get("Location"), len(r.get_data()))

        existing = post(TEST_EMAIL)             # already registered by fixture
        fresh    = post("nobody-new@example.com")
        assert existing == fresh
        assert existing[0] == 302

        # The real owner is still told what happened, out of band.
        assert ("exists", TEST_EMAIL) in sent
        assert ("verify", "nobody-new@example.com") in sent

    def test_duplicate_registration_does_not_leak_in_body(self, client, monkeypatch):
        import serve
        monkeypatch.setattr(serve, "send_account_exists_email", lambda *a, **k: True)
        csrf = get_csrf(client)
        r = client.post("/register", data={
            "email": TEST_EMAIL, "password": "somepassword1",
            "confirm": "somepassword1", "_csrf_token": csrf},
            follow_redirects=True)
        assert b"already exists" not in r.data

    def test_genuine_input_errors_are_still_reported(self, client):
        csrf = get_csrf(client)
        r = client.post("/register", data={
            "email": "not-an-email", "password": "somepassword1",
            "confirm": "somepassword1", "_csrf_token": csrf})
        assert r.status_code == 200
        assert b"Invalid email address" in r.data


class TestLoginDoesNotEnumerate:
    def test_wrong_password_responses_are_identical(self, client, tmp_path):
        import serve, user_store as us
        from conftest import _make_verified_user
        data_dir = tmp_path / "data"
        us.UserStore.create_user("unver@example.com", "unverpassword1")
        susp = _make_verified_user(data_dir, email="susp@example.com",
                                   password="susppassword1")
        us.UserStore.suspend_user(susp["id"], True)
        csrf = get_csrf(client)

        prints = set()
        for email in [TEST_EMAIL, "unver@example.com", "susp@example.com",
                      "ghost@example.com"]:
            serve._rate_limit.clear()
            r = client.post("/auth/login", data={
                "email": email, "password": "definitely-wrong",
                "_csrf_token": csrf})
            assert b"Invalid email or password" in r.data
            for leak in [b"verify your email", b"suspended", b"locked"]:
                assert leak not in r.data.lower(), (email, leak)
            prints.add((r.status_code, r.headers.get("Location"), len(r.get_data())))
        assert len(prints) == 1, prints


class TestAdminUserIdTraversal:
    """A user_id from the URL reaches shutil.rmtree() via delete_user(), so a
    value like '..' must never resolve to a path outside DATA_DIR."""

    def _admin(self, client, tmp_path):
        from conftest import _make_verified_user, _AutoCsrfClient
        admin = _make_verified_user(tmp_path / "data", email="admin@example.com",
                                    password="adminpassword1", admin=True)
        with client.session_transaction() as sess:
            sess["user_id"] = admin["id"]; sess["csrf_token"] = "tc"
        return _AutoCsrfClient(client, "tc"), admin

    def test_dotdot_user_id_is_rejected(self, client, tmp_path):
        import user_store as us
        admin_client, _ = self._admin(client, tmp_path)
        data_dir = tmp_path / "data"
        canary = tmp_path / "canary.txt"
        canary.write_text("keep")

        r = admin_client.post("/admin/users/../delete?confirm=yes",
                              data={"_csrf_token": "tc"})
        assert r.status_code == 404
        assert canary.exists()      # nothing outside DATA_DIR was touched
        assert data_dir.exists()

    def test_non_uuid_user_id_is_rejected(self, client, tmp_path):
        admin_client, _ = self._admin(client, tmp_path)
        for probe in ["notauuid", "../../etc", "."]:
            r = admin_client.post(f"/admin/users/{probe}/suspend",
                                  data={"_csrf_token": "tc"})
            assert r.status_code == 404, probe

    def test_user_dir_rejects_traversal(self):
        import user_store as us
        for bad in ["..", ".", "a/b", "a\\b", ""]:
            with pytest.raises(ValueError):
                us._user_dir(bad)

    def test_is_valid_user_id(self):
        import user_store as us
        assert us.is_valid_user_id("45e23980-168a-45ed-a08c-4e64c7a21005")
        for bad in ["..", "notauuid", "", None, "45e23980168a45eda08c4e64c7a21005/x"]:
            assert not us.is_valid_user_id(bad)


class TestTaskTextSanitisation:
    """tasks.md is line-oriented, so no field may smuggle in a newline and
    forge extra task lines or a '## Archive' section header."""

    def test_newline_in_task_text_cannot_forge_a_task(self, authed_client):
        import user_store as us
        from task_manager import read_tasks
        before = len(read_tasks(us._user_dir(
            us.UserStore.get_by_email(TEST_EMAIL)["id"]) / "tasks.md"))

        r = authed_client.post("/tasks/add", data=json.dumps(
            {"text": "benign\n- [ ] FORGED #id:deadbe\n## Archive"}),
            content_type="application/json")
        assert r.status_code == 200

        tasks = read_tasks(us._user_dir(
            us.UserStore.get_by_email(TEST_EMAIL)["id"]) / "tasks.md")
        assert len(tasks) == before + 1          # exactly one task added
        assert not any(t.description == "FORGED" for t in tasks)

    def test_newline_in_description_update_is_flattened(self, authed_client):
        import user_store as us
        from task_manager import read_tasks
        tid = _task_id(authed_client, 0)
        authed_client.post("/tasks/update", data=json.dumps(
            {"task_id": tid, "field": "description",
             "value": "one\n- [ ] TWO #id:beef01"}),
            content_type="application/json")
        tasks = read_tasks(us._user_dir(
            us.UserStore.get_by_email(TEST_EMAIL)["id"]) / "tasks.md")
        assert not any(t.description == "TWO" for t in tasks)

    def test_multiline_notes_are_still_allowed(self, authed_client):
        """Notes are legitimately multi-line — only single-line fields are flattened."""
        tid = _task_id(authed_client, 0)
        r = authed_client.post("/tasks/update", data=json.dumps(
            {"task_id": tid, "field": "notes", "value": "line one\nline two"}),
            content_type="application/json")
        assert r.status_code == 200
        r2 = authed_client.get("/")
        assert b"line two" in r2.data


class TestRequestLimitsAndHeaders:
    def test_oversized_body_is_rejected(self, authed_client):
        r = authed_client.post("/tasks/add",
                               data=json.dumps({"text": "A" * (2 * 1024 * 1024)}),
                               content_type="application/json")
        assert r.status_code == 413

    def test_security_headers_present(self, client):
        r = client.get("/auth/login")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        assert r.headers["Referrer-Policy"] == "same-origin"


class TestTwoFactorBruteForce:
    def test_2fa_attempts_are_rate_limited(self, client, tmp_path):
        import pyotp, serve, user_store as us
        serve._rate_limit.clear()
        user = us.UserStore.get_by_email(TEST_EMAIL)
        us.UserStore.enable_totp(user["id"], pyotp.random_base32())
        with client.session_transaction() as sess:
            sess["pending_2fa_user_id"] = user["id"]
            sess["csrf_token"] = "tc"

        codes = [f"{n:06d}" for n in range(15)]
        statuses = [client.post("/auth/2fa", data={"code": c, "_csrf_token": "tc"}).status_code
                    for c in codes]
        assert all(s == 200 for s in statuses)   # never redirects (never accepted)
        # After the per-account cap (10/5min) the view short-circuits.
        last = client.post("/auth/2fa", data={"code": "999999", "_csrf_token": "tc"})
        assert b"Too many attempts" in last.data
        serve._rate_limit.clear()


class TestTotpSecretNotAttackerSupplied:
    def test_query_param_secret_is_ignored(self, authed_client):
        r = authed_client.get("/auth/2fa/setup?secret=ATTACKERSUPPLIEDSECRETAAA")
        assert r.status_code == 200
        assert b"ATTACKERSUPPLIEDSECRETAAA" not in r.data


# ---------------------------------------------------------------------------
# Security — _ip() and _safe_next (H2, L3)
# ---------------------------------------------------------------------------

class TestIpAndSafeNext:
    def test_safe_next_rejects_leading_backslashes(self):
        """Browsers normalise '\\' to '/', so these are protocol-relative."""
        import serve
        with serve.app.test_request_context("/"):
            assert serve._safe_next("\\\\evil.com") == "/"
            assert serve._safe_next("\\/evil.com") == "/"
            assert serve._safe_next("/tasks\\evil") == "/"

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
            assert serve._safe_next("//evil.com/steal") == "/"

    def test_safe_next_rejects_backslash_protocol_relative(self):
        import serve
        with serve.app.test_request_context("/"):
            assert serve._safe_next("/\\evil.com") == "/"

    def test_safe_next_rejects_absolute_url(self):
        import serve
        with serve.app.test_request_context("/"):
            assert serve._safe_next("https://evil.com") == "/"

    def test_safe_next_allows_relative_path(self):
        import serve
        with serve.app.test_request_context("/"):
            assert serve._safe_next("/tasks") == "/tasks"


# ---------------------------------------------------------------------------
# Admin — bulk delete unverified (bot/scanner sign-up cleanup)
# ---------------------------------------------------------------------------

class TestAdminBulkDeleteUnverified:
    def _admin_client(self, client, tmp_path):
        """An authenticated session for an admin user, plus some other
        accounts to exercise the bulk-delete against."""
        import user_store as us
        from conftest import _make_verified_user

        data_dir = tmp_path / "data"
        admin = _make_verified_user(data_dir, email="admin@example.com",
                                     password="adminpassword1", admin=True)
        with client.session_transaction() as sess:
            sess["user_id"]    = admin["id"]
            sess["csrf_token"] = "testcsrf"
        from conftest import _AutoCsrfClient
        return _AutoCsrfClient(client, "testcsrf"), admin

    def test_deletes_only_unverified_non_admin(self, client, tmp_path):
        import user_store as us
        admin_client, admin = self._admin_client(client, tmp_path)

        unverified = us.UserStore.create_user("bot1@example.com", "botpassword1")
        us.UserStore.create_user("bot2@example.com", "botpassword1")
        verified = us.UserStore.create_user("real@example.com", "realpassword1")
        p = us._read_json(us._profile_path(verified["id"]))
        p["verified"] = True
        us._write_json(us._profile_path(verified["id"]), p)

        r = admin_client.post("/admin/users/bulk-delete-unverified?confirm=yes",
                              data={"_csrf_token": "testcsrf"})
        assert r.status_code == 302

        assert us.UserStore.get_user(unverified["id"]) is None
        assert us.UserStore.get_user(verified["id"]) is not None   # untouched
        assert us.UserStore.get_user(admin["id"]) is not None      # untouched

    def test_requires_confirm_param(self, client, tmp_path):
        import user_store as us
        admin_client, admin = self._admin_client(client, tmp_path)
        stray = us.UserStore.create_user("bot@example.com", "botpassword1")

        r = admin_client.post("/admin/users/bulk-delete-unverified",
                              data={"_csrf_token": "testcsrf"})
        assert r.status_code == 302
        assert us.UserStore.get_user(stray["id"]) is not None  # nothing deleted

    def test_requires_admin(self, authed_client):
        r = authed_client.post("/admin/users/bulk-delete-unverified?confirm=yes")
        assert r.status_code in (302, 403)


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
    def _get_task(self, authed_client, index):
        from task_manager import read_tasks
        import user_store as us
        user = us.UserStore.get_by_email(TEST_EMAIL)
        tasks_file = us._user_dir(user["id"]) / "tasks.md"
        return read_tasks(tasks_file)[index]

    def _bulk_with_hashes(self, client, action, task_ids, hashes, value=""):
        return client.post(
            "/tasks/bulk-update",
            data=json.dumps({"action": action, "task_ids": task_ids,
                             "value": value, "task_hashes": hashes}),
            content_type="application/json",
        )

    def test_bulk_correct_hash_succeeds(self, authed_client):
        task = self._get_task(authed_client, 0)
        r = self._bulk_with_hashes(authed_client, "set-priority", [task.id],
                                   {task.id: task.content_hash}, "high")
        assert r.get_json()["ok"] is True

    def test_bulk_wrong_hash_returns_409(self, authed_client):
        task = self._get_task(authed_client, 0)
        r = self._bulk_with_hashes(authed_client, "set-priority", [task.id],
                                   {task.id: "deadbeef"}, "high")
        assert r.status_code == 409

    def test_bulk_no_hashes_still_works(self, authed_client):
        task = self._get_task(authed_client, 0)
        r = self._bulk_with_hashes(authed_client, "set-priority", [task.id], {}, "high")
        assert r.get_json()["ok"] is True
