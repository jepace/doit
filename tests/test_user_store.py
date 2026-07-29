"""
Tests for user_store.py — auth, token index, timing equalization.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import TEST_EMAIL, TEST_PASSWORD, _make_verified_user


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """UserStore with DATA_DIR patched to a temp dir."""
    import user_store as us

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(us, "DATA_DIR", data_dir)
    return us.UserStore


@pytest.fixture()
def user(store, tmp_path):
    """A verified user created in the temp store."""
    import user_store as us
    data_dir = tmp_path / "data"
    return _make_verified_user(data_dir)


# ---------------------------------------------------------------------------
# Authentication — timing equalization (H3a)
# ---------------------------------------------------------------------------

class TestAuthenticate:
    def test_success(self, store, user):
        u, err = store.authenticate(TEST_EMAIL, TEST_PASSWORD)
        assert u is not None
        assert err == ""

    def test_wrong_password_returns_generic_message(self, store, user):
        u, err = store.authenticate(TEST_EMAIL, "wrongpassword")
        assert u is None
        assert err == "Invalid email or password."

    def test_missing_user_returns_same_message_as_wrong_password(self, store):
        """Timing equalization: missing user must return the same error string."""
        u, err = store.authenticate("nobody@example.com", "irrelevant")
        assert u is None
        assert err == "Invalid email or password."

    def test_suspended_user_cannot_login(self, store, user):
        store.suspend_user(user["id"], True)
        u, err = store.authenticate(TEST_EMAIL, TEST_PASSWORD)
        assert u is None
        assert "suspended" in err.lower()

    def test_unverified_user_cannot_login(self, store, tmp_path):
        import user_store as us
        data_dir = tmp_path / "data"
        raw = store.create_user("unverified@example.com", TEST_PASSWORD)
        u, err = store.authenticate("unverified@example.com", TEST_PASSWORD)
        assert u is None
        assert "verify" in err.lower()


class TestNoAccountEnumeration:
    """Account state must only be disclosed once the password is verified,
    otherwise the login form becomes an account-existence oracle."""

    def test_wrong_password_is_generic_for_every_account_state(self, store, tmp_path):
        data_dir = tmp_path / "data"
        verified = _make_verified_user(data_dir, email="v@example.com",
                                       password="verifiedpass1")
        store.create_user("u@example.com", "unverifiedpass1")          # unverified
        susp = _make_verified_user(data_dir, email="s@example.com",
                                   password="susppass1234")
        store.suspend_user(susp["id"], True)

        errors = set()
        for email in ["v@example.com", "u@example.com", "s@example.com",
                      "ghost@example.com"]:
            u, err = store.authenticate(email, "wrong-password")
            assert u is None
            errors.add(err)
        # One identical message regardless of whether the account exists,
        # is unverified, or is suspended.
        assert errors == {store.GENERIC_AUTH_ERROR}

    def test_locked_account_is_generic_on_wrong_password(self, store, user):
        for _ in range(5):
            store.record_failed_login(user["id"])
        assert store.is_locked(user["id"])
        u, err = store.authenticate(TEST_EMAIL, "wrong-password")
        assert u is None
        assert err == store.GENERIC_AUTH_ERROR      # not "locked"

    def test_locked_account_is_disclosed_on_correct_password(self, store, user):
        for _ in range(5):
            store.record_failed_login(user["id"])
        u, err = store.authenticate(TEST_EMAIL, TEST_PASSWORD)
        assert u is None
        assert "locked" in err.lower()              # owner may be told

    def test_failed_attempts_on_locked_account_do_not_extend_the_lock(self, store, user):
        import user_store as us
        for _ in range(5):
            store.record_failed_login(user["id"])
        before = us._read_json(us._profile_path(user["id"]))["failed_logins"]
        store.authenticate(TEST_EMAIL, "wrong-password")
        after = us._read_json(us._profile_path(user["id"]))["failed_logins"]
        assert after == before

    def test_duplicate_registration_raises_distinguishable_type(self, store, user):
        import user_store as us
        # A dedicated type so the web layer can suppress this one message while
        # still surfacing genuine input errors.
        with pytest.raises(us.EmailTakenError):
            store.create_user(TEST_EMAIL, "anotherpassword1")
        assert issubclass(us.EmailTakenError, ValueError)

    def test_input_validation_errors_are_not_email_taken(self, store):
        import user_store as us
        for email, pw in [("not-an-email", "goodpassword1"),
                          ("fine@example.com", "short")]:
            with pytest.raises(ValueError) as ei:
                store.create_user(email, pw)
            assert not isinstance(ei.value, us.EmailTakenError)


# ---------------------------------------------------------------------------
# Token index — O(1) lookup, no reuse (H4)
# ---------------------------------------------------------------------------

class TestVerifyTokenIndex:
    def test_token_can_be_consumed(self, store, tmp_path):
        import user_store as us
        data_dir = tmp_path / "data"
        raw = store.create_user("new@example.com", TEST_PASSWORD)
        token = store.create_verify_token(raw["id"])

        result = store.consume_verify_token(token)
        assert result is not None
        assert result["verified"] is True

    def test_token_index_file_created(self, store, tmp_path):
        import user_store as us
        data_dir = tmp_path / "data"
        raw = store.create_user("idx@example.com", TEST_PASSWORD)
        store.create_verify_token(raw["id"])

        idx_path = data_dir / "token_index.json"
        assert idx_path.exists()

    def test_consumed_token_cannot_be_reused(self, store, tmp_path):
        import user_store as us
        data_dir = tmp_path / "data"
        raw = store.create_user("reuse@example.com", TEST_PASSWORD)
        token = store.create_verify_token(raw["id"])

        store.consume_verify_token(token)
        result = store.consume_verify_token(token)
        assert result is None

    def test_invalid_token_returns_none(self, store):
        assert store.consume_verify_token("notavalidtoken") is None

    def test_new_token_replaces_old_in_index(self, store, tmp_path):
        import user_store as us
        data_dir = tmp_path / "data"
        raw = store.create_user("replace@example.com", TEST_PASSWORD)
        old_token = store.create_verify_token(raw["id"])
        _new_token = store.create_verify_token(raw["id"])

        # Old token should no longer work
        assert store.consume_verify_token(old_token) is None


class TestResetTokenIndex:
    def test_reset_token_can_be_consumed(self, store, user):
        token, u = store.create_reset_token(TEST_EMAIL)
        assert token is not None
        ok = store.consume_reset_token(token, "newpassword1")
        assert ok is True

    def test_consumed_reset_token_cannot_be_reused(self, store, user):
        token, _ = store.create_reset_token(TEST_EMAIL)
        store.consume_reset_token(token, "newpassword1")
        ok = store.consume_reset_token(token, "anotherpassword")
        assert ok is False

    def test_invalid_reset_token_returns_false(self, store, user):
        assert store.consume_reset_token("garbage", "newpassword1") is False

    def test_reset_token_for_unknown_email_returns_none(self, store):
        token, u = store.create_reset_token("ghost@example.com")
        assert token is None and u is None

    def test_reset_changes_password(self, store, user):
        token, _ = store.create_reset_token(TEST_EMAIL)
        store.consume_reset_token(token, "brandnewpass")
        u, err = store.authenticate(TEST_EMAIL, "brandnewpass")
        assert u is not None


class TestApiToken:
    def test_generated_token_resolves_to_user(self, store, user):
        token = store.create_api_token(user["id"])
        assert token is not None
        found = store.get_user_by_api_token(token)
        assert found is not None
        assert found["id"] == user["id"]

    def test_token_is_reusable_not_consumed(self, store, user):
        token = store.create_api_token(user["id"])
        assert store.get_user_by_api_token(token) is not None
        # Unlike verify/reset tokens, looking it up again must still work.
        assert store.get_user_by_api_token(token) is not None

    def test_invalid_token_returns_none(self, store):
        assert store.get_user_by_api_token("not-a-real-token") is None

    def test_empty_token_returns_none(self, store):
        assert store.get_user_by_api_token("") is None

    def test_regenerating_invalidates_old_token(self, store, user):
        old_token = store.create_api_token(user["id"])
        new_token = store.create_api_token(user["id"])
        assert old_token != new_token
        assert store.get_user_by_api_token(old_token) is None
        assert store.get_user_by_api_token(new_token) is not None

    def test_revoke_invalidates_token(self, store, user):
        token = store.create_api_token(user["id"])
        store.revoke_api_token(user["id"])
        assert store.get_user_by_api_token(token) is None

    def test_revoke_without_token_is_a_noop(self, store, user):
        store.revoke_api_token(user["id"])  # no token ever generated
        assert store.get_user(user["id"]) is not None
