"""Tests for the agent-facing REST API (/api/v1) and MCP server (/mcp)."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import TEST_EMAIL, _make_verified_user


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    import agent_api
    agent_api._hits.clear()
    yield
    agent_api._hits.clear()


def _token(scopes=("read", "write"), email=TEST_EMAIL, name="test"):
    import user_store as us
    user = us.UserStore.get_by_email(email)
    plain, _ = us.UserStore.create_agent_token(user["id"], name, scopes)
    return plain


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _tasks_file(email=TEST_EMAIL):
    import user_store as us
    return us._user_dir(us.UserStore.get_by_email(email)["id"]) / "tasks.md"


def _mcp(client, token, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "method": method, "id": msg_id}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", data=json.dumps(body),
                       content_type="application/json", headers=_h(token))


def _call(client, token, name, **args):
    r = _mcp(client, token, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.data
    return r.get_json()["result"]


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

class TestTokens:
    def test_token_is_shown_once_and_stored_hashed(self, client):
        import user_store as us
        uid = us.UserStore.get_by_email(TEST_EMAIL)["id"]
        plain, rec = us.UserStore.create_agent_token(uid, "x", ["read"])
        assert plain.startswith("doit_")
        assert "hash" not in rec
        assert plain not in us._profile_path(uid).read_text()

    def test_unknown_scopes_are_dropped_and_empty_rejected(self, client):
        import user_store as us
        uid = us.UserStore.get_by_email(TEST_EMAIL)["id"]
        _, rec = us.UserStore.create_agent_token(uid, "x", ["read", "admin", "root"])
        assert rec["scopes"] == ["read"]
        with pytest.raises(ValueError):
            us.UserStore.create_agent_token(uid, "x", ["admin"])

    def test_revoked_token_stops_working(self, client):
        import user_store as us
        uid = us.UserStore.get_by_email(TEST_EMAIL)["id"]
        plain, rec = us.UserStore.create_agent_token(uid, "x", ["read"])
        assert client.get("/api/v1/tasks", headers=_h(plain)).status_code == 200
        assert us.UserStore.revoke_agent_token(uid, rec["id"])
        assert client.get("/api/v1/tasks", headers=_h(plain)).status_code == 401

    def test_suspended_user_token_refused(self, client):
        import user_store as us
        tok = _token(["read"])
        us.UserStore.suspend_user(us.UserStore.get_by_email(TEST_EMAIL)["id"], True)
        assert client.get("/api/v1/tasks", headers=_h(tok)).status_code == 401

    def test_legacy_quick_add_token_is_write_only(self, client):
        import user_store as us
        uid = us.UserStore.get_by_email(TEST_EMAIL)["id"]
        legacy = us.UserStore.create_api_token(uid)
        assert client.get("/api/v1/tasks", headers=_h(legacy)).status_code == 403
        r = client.post("/api/v1/tasks", json={"description": "via legacy"}, headers=_h(legacy))
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# Auth & transport security
# ---------------------------------------------------------------------------

class TestAuth:
    @pytest.mark.parametrize("hdr", [None, "Bearer ", "Bearer nope", "Basic abc", "doit_x"])
    def test_bad_or_missing_token_is_401(self, client, hdr):
        headers = {"Authorization": hdr} if hdr is not None else {}
        r = client.get("/api/v1/tasks", headers=headers)
        assert r.status_code == 401
        assert "Bearer" in r.headers.get("WWW-Authenticate", "")
        assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           headers=headers).status_code == 401

    def test_session_cookie_alone_grants_nothing(self, authed_client):
        """A logged-in browser must not be able to use the API without a token —
        otherwise any web page could drive it via the user's cookies."""
        assert authed_client.get("/api/v1/tasks").status_code == 401
        assert authed_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                                "method": "ping"}).status_code == 401

    def test_token_works_even_with_a_session_present(self, authed_client):
        """No CSRF false-positive when the client also carries a session."""
        tok = _token(["write"])
        r = authed_client.post("/api/v1/tasks", json={"description": "x"}, headers=_h(tok))
        assert r.status_code == 201

    def test_foreign_origin_is_refused(self, client):
        tok = _token(["read"])
        r = client.get("/api/v1/tasks", headers={**_h(tok), "Origin": "https://evil.example"})
        assert r.status_code == 403
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        headers={**_h(tok), "Origin": "https://evil.example"})
        assert r.status_code == 403

    def test_failed_auth_is_throttled(self, client):
        codes = [client.get("/api/v1/tasks", headers=_h(f"doit_wrong{i}")).status_code
                 for i in range(25)]
        assert codes[0] == 401 and codes[-1] == 429

    def test_users_are_isolated(self, client, tmp_path):
        other = _make_verified_user(tmp_path / "data", email="other@example.com",
                                    password="otherpassword1")
        mine = _token(["read", "write", "delete"])
        theirs = _token(["read"], email="other@example.com")
        created = client.post("/api/v1/tasks", json={"description": "mine only"},
                              headers=_h(mine)).get_json()
        assert client.get(f"/api/v1/tasks/{created['id']}", headers=_h(theirs)).status_code == 404
        listed = client.get("/api/v1/tasks?status=all", headers=_h(theirs)).get_json()
        assert "mine only" not in [t["description"] for t in listed["tasks"]]


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

class TestScopes:
    def test_read_only_token_cannot_write_or_delete(self, client):
        tok = _token(["read"])
        assert client.post("/api/v1/tasks", json={"description": "x"},
                           headers=_h(tok)).status_code == 403
        tid = client.get("/api/v1/tasks", headers=_h(tok)).get_json()["tasks"][0]["id"]
        assert client.patch(f"/api/v1/tasks/{tid}", json={"priority": "low"},
                            headers=_h(tok)).status_code == 403
        assert client.delete(f"/api/v1/tasks/{tid}", headers=_h(tok)).status_code == 403

    def test_write_token_cannot_delete(self, client):
        tok = _token(["read", "write"])
        tid = client.get("/api/v1/tasks", headers=_h(tok)).get_json()["tasks"][0]["id"]
        assert client.delete(f"/api/v1/tasks/{tid}", headers=_h(tok)).status_code == 403

    def test_mcp_hides_and_blocks_out_of_scope_tools(self, client):
        tok = _token(["read"])
        names = {t["name"] for t in _mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
        assert names == {"list_tasks", "get_task", "list_contexts"}
        r = _mcp(client, tok, "tools/call", {"name": "add_task",
                                             "arguments": {"description": "x"}})
        assert r.get_json()["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# REST behaviour
# ---------------------------------------------------------------------------

class TestRest:
    def test_list_defaults_to_open_and_sorts(self, client):
        data = client.get("/api/v1/tasks", headers=_h(_token(["read"]))).get_json()
        assert data["tasks"] and all(not t["done"] for t in data["tasks"])
        dues = [t["due"] for t in data["tasks"] if t["due"]]
        assert dues == sorted(dues)

    def test_filters(self, client):
        tok = _token(["read"])
        phone = client.get("/api/v1/tasks?context=phone", headers=_h(tok)).get_json()["tasks"]
        assert phone and all(t["context"] == "phone" for t in phone)
        found = client.get("/api/v1/tasks?search=MILK", headers=_h(tok)).get_json()["tasks"]
        assert [t["description"] for t in found] == ["Buy milk"]
        due = client.get("/api/v1/tasks?due_before=2026-05-31", headers=_h(tok)).get_json()["tasks"]
        assert all(t["due"] and t["due"] <= "2026-05-31" for t in due)

    def test_add_with_fields_round_trips(self, client):
        tok = _token(["read", "write"])
        r = client.post("/api/v1/tasks", headers=_h(tok), json={
            "description": "Renew passport", "due": "2026-12-01", "priority": "high",
            "context": "errands", "recurrence": "10y", "starred": True,
            "notes": "Bring old passport\nand photos"})
        assert r.status_code == 201
        t = r.get_json()
        assert (t["due"], t["priority"], t["context"], t["recurrence"], t["starred"]) == \
               ("2026-12-01", "high", "errands", "10y", True)
        assert t["notes"] == "Bring old passport\nand photos"   # exactly as written
        assert t["start"] == date.today().isoformat()
        assert client.get(f"/api/v1/tasks/{t['id']}", headers=_h(tok)).get_json()["id"] == t["id"]

    def test_notes_round_trip_is_lossless(self, client):
        tok = _token(["read", "write"])
        notes = "Line one\n  - [ ] indented subtask\nLine three"
        t = client.post("/api/v1/tasks", headers=_h(tok),
                        json={"description": "x", "notes": notes}).get_json()
        assert t["notes"] == notes
        t2 = client.patch(f"/api/v1/tasks/{t['id']}", headers=_h(tok),
                          json={"notes": t["notes"]}).get_json()
        assert t2["notes"] == notes

    def test_patch_sets_and_clears_fields(self, client):
        tok = _token(["read", "write"])
        t = client.post("/api/v1/tasks", headers=_h(tok),
                        json={"description": "x", "due": "2026-09-09"}).get_json()
        r = client.patch(f"/api/v1/tasks/{t['id']}", headers=_h(tok),
                         json={"due": None, "priority": "top", "description": "y"})
        out = r.get_json()
        assert r.status_code == 200
        assert (out["due"], out["priority"], out["description"]) == (None, "top", "y")

    def test_stale_version_is_rejected(self, client):
        tok = _token(["read", "write"])
        t = client.post("/api/v1/tasks", headers=_h(tok), json={"description": "x"}).get_json()
        client.patch(f"/api/v1/tasks/{t['id']}", headers=_h(tok), json={"priority": "low"})
        r = client.patch(f"/api/v1/tasks/{t['id']}", headers=_h(tok),
                         json={"priority": "high", "version": t["version"]})
        assert r.status_code == 409

    def test_complete_recurring_creates_next_and_archives(self, client):
        import user_store as us
        from task_manager import read_tasks
        tok = _token(["read", "write"])
        t = client.post("/api/v1/tasks", headers=_h(tok),
                        json={"description": "Water plants", "due": "2026-08-01",
                              "recurrence": "1w"}).get_json()
        r = client.post(f"/api/v1/tasks/{t['id']}/complete", headers=_h(tok)).get_json()
        assert r["task"]["done"] is True
        assert r["next_occurrence"]["due"] == "2026-08-08"
        assert r["next_occurrence"]["id"] != t["id"]
        archive = _tasks_file().parent / "archive.md"
        assert t["id"] in [x.id for x in read_tasks(archive)]
        # completing twice is harmless
        again = client.post(f"/api/v1/tasks/{t['id']}/complete", headers=_h(tok)).get_json()
        assert again["next_occurrence"] is None

    def test_reopen_moves_back(self, client):
        tok = _token(["read", "write"])
        t = client.post("/api/v1/tasks", headers=_h(tok), json={"description": "x"}).get_json()
        client.post(f"/api/v1/tasks/{t['id']}/complete", headers=_h(tok))
        r = client.post(f"/api/v1/tasks/{t['id']}/reopen", headers=_h(tok)).get_json()
        assert r["done"] is False
        open_ids = [x["id"] for x in client.get("/api/v1/tasks", headers=_h(tok)).get_json()["tasks"]]
        assert t["id"] in open_ids

    def test_delete(self, client):
        tok = _token(["read", "write", "delete"])
        t = client.post("/api/v1/tasks", headers=_h(tok), json={"description": "x"}).get_json()
        assert client.delete(f"/api/v1/tasks/{t['id']}", headers=_h(tok)).status_code == 200
        assert client.get(f"/api/v1/tasks/{t['id']}", headers=_h(tok)).status_code == 404

    def test_contexts(self, client):
        data = client.get("/api/v1/contexts", headers=_h(_token(["read"]))).get_json()
        assert "phone" in data["contexts"] and "acme" in data["projects"]

    def test_unknown_task_id_is_404(self, client):
        tok = _token(["read"])
        for bad in ["ffffff", "../../etc", "x" * 100]:
            assert client.get(f"/api/v1/tasks/{bad}", headers=_h(tok)).status_code == 404


# ---------------------------------------------------------------------------
# Input validation — an agent must not be able to corrupt tasks.md
# ---------------------------------------------------------------------------

class TestValidation:
    @pytest.mark.parametrize("fields", [
        {"description": "a\n- [ ] FORGED #id:aaaaaa"},
        {"description": "sneaky #due:2020-01-01"},
        {"description": ""},
        {"description": "x" * 600},
        {"description": "ok", "due": "tomorrow"},
        {"description": "ok", "due": "2026-02-30"},
        {"description": "ok", "priority": "urgent"},
        {"description": "ok", "context": "two words"},
        {"description": "ok", "context": "#ctx"},
        {"description": "ok", "recurrence": "every day"},
        {"description": "ok", "starred": "yes"},
        {"description": "ok", "section": "Archive"},
        {"description": "ok", "bogus": 1},
        {"description": ["not", "a", "string"]},
    ])
    def test_rejects_bad_input_without_writing(self, client, fields):
        tok = _token(["read", "write"])
        before = _tasks_file().read_text()
        r = client.post("/api/v1/tasks", headers=_h(tok), json=fields)
        assert r.status_code == 400, fields
        assert r.get_json()["error"]["code"] in ("invalid_field", "invalid_request")
        assert _tasks_file().read_text() == before

    def test_notes_cannot_forge_tasks(self, client):
        from task_manager import read_tasks
        tok = _token(["read", "write"])
        t = client.post("/api/v1/tasks", headers=_h(tok),
                        json={"description": "x", "notes": "- [ ] FORGED\n## Archive"}).get_json()
        descs = [x.description for x in read_tasks(_tasks_file())]
        assert "FORGED" not in descs
        assert "## Archive" not in [l.strip() for l in _tasks_file().read_text().splitlines()
                                    if not l.startswith(" ")]

    def test_malformed_json_body(self, client):
        r = client.post("/api/v1/tasks", data="{nope", content_type="application/json",
                        headers=_h(_token(["write"])))
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# MCP protocol
# ---------------------------------------------------------------------------

class TestMcp:
    def test_initialize_negotiates_version(self, client):
        tok = _token(["read"])
        res = _mcp(client, tok, "initialize", {"protocolVersion": "2025-03-26",
                                               "capabilities": {},
                                               "clientInfo": {"name": "t", "version": "1"}}
                   ).get_json()["result"]
        assert res["protocolVersion"] == "2025-03-26"
        assert res["capabilities"]["tools"] is not None
        assert "data" in res["instructions"]          # prompt-injection guidance present
        res = _mcp(client, tok, "initialize", {"protocolVersion": "1999-01-01"}).get_json()["result"]
        assert res["protocolVersion"] == "2025-06-18"

    def test_notification_gets_202(self, client):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers=_h(_token(["read"])))
        assert r.status_code == 202 and r.data == b""

    def test_tools_have_schemas_and_annotations(self, client):
        tools = _mcp(client, _token(["read", "write", "delete"]), "tools/list"
                     ).get_json()["result"]["tools"]
        by = {t["name"]: t for t in tools}
        assert set(by) == {"list_tasks", "get_task", "list_contexts", "add_task",
                           "update_task", "complete_task", "reopen_task", "delete_task"}
        assert by["delete_task"]["annotations"]["destructiveHint"] is True
        assert by["list_tasks"]["annotations"]["readOnlyHint"] is True
        assert all(t["inputSchema"]["type"] == "object" for t in tools)
        assert all("scope" not in t for t in tools)

    def test_end_to_end_tool_flow(self, client):
        tok = _token(["read", "write"])
        added = _call(client, tok, "add_task", description="Call mom", context="phone",
                      due="2026-10-02")
        assert added["isError"] is False
        tid = added["structuredContent"]["id"]
        assert json.loads(added["content"][0]["text"])["id"] == tid

        listed = _call(client, tok, "list_tasks", context="phone")
        assert tid in [t["id"] for t in listed["structuredContent"]["tasks"]]

        upd = _call(client, tok, "update_task", id=tid, priority="high")
        assert upd["structuredContent"]["priority"] == "high"

        done = _call(client, tok, "complete_task", id=tid)
        assert done["structuredContent"]["task"]["done"] is True

    def test_tool_errors_are_reported_to_the_model(self, client):
        res = _call(client, _token(["read", "write"]), "add_task",
                    description="bad", due="someday")
        assert res["isError"] is True
        assert "YYYY-MM-DD" in res["content"][0]["text"]
        res = _call(client, _token(["read"]), "get_task", id="000000")
        assert res["isError"] is True and "not_found" in res["content"][0]["text"]

    def test_unknown_method_and_bad_json(self, client):
        tok = _token(["read"])
        assert _mcp(client, tok, "resources/list").get_json()["error"]["code"] == -32601
        r = client.post("/mcp", data="{bad", content_type="application/json", headers=_h(tok))
        assert r.status_code == 400 and r.get_json()["error"]["code"] == -32700

    def test_get_is_405(self, client):
        assert client.get("/mcp", headers=_h(_token(["read"]))).status_code == 405


# ---------------------------------------------------------------------------
# Settings UI
# ---------------------------------------------------------------------------

class TestSettingsUi:
    def _post(self, c, **form):
        with c.session_transaction() as s:
            csrf = s["csrf_token"]
        return c.post("/settings", data={"_csrf_token": csrf, **form})

    def test_create_shows_token_once_then_list_and_revoke(self, authed_client):
        import user_store as us
        r = self._post(authed_client, action="agent_token_create",
                       token_name="My Claude", scope_read="on", scope_write="on")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "doit_" in html and "My Claude" in html
        plain = html.split('value="doit_')[1].split('"')[0]

        page = authed_client.get("/settings").get_data(as_text=True)
        assert "My Claude" in page and plain not in page      # never shown again

        uid = us.UserStore.get_by_email(TEST_EMAIL)["id"]
        rec = us.UserStore.list_agent_tokens(uid)[0]
        assert rec["scopes"] == ["read", "write"]              # delete not granted
        self._post(authed_client, action="agent_token_revoke", token_id=rec["id"])
        assert us.UserStore.list_agent_tokens(uid) == []

    def test_create_requires_a_scope(self, authed_client):
        r = self._post(authed_client, action="agent_token_create", token_name="none")
        assert b"at least one permission" in r.data
