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
