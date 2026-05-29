#!/usr/bin/env python3
"""
UserStore — multi-user data layer for doit.

All data lives under:
  data/{user_id}/profile.json
  data/{user_id}/preferences.json
  data/{user_id}/tasks.md
  data/email_index.json
"""

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from werkzeug.security import generate_password_hash, check_password_hash

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

# Lock account after this many consecutive failed logins
_MAX_FAILED_LOGINS = 5
# Lock duration in minutes
_LOCKOUT_MINUTES = 15

# Per-key threading locks — keyed by user_id or "email_index"
_locks: dict = {}
_locks_meta = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _locks_meta:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class UserStore:
    """Classmethods-only stateless user store. All I/O goes through here."""

    # ── Lock helpers ──────────────────────────────────────────

    @classmethod
    def get_user_lock(cls, user_id: str) -> threading.Lock:
        """Public: get the per-user lock (used by serve.py for task file writes too)."""
        return _get_lock(user_id)

    # ── Paths ─────────────────────────────────────────────────

    @classmethod
    def _user_dir(cls, user_id: str) -> Path:
        return DATA_DIR / user_id

    @classmethod
    def _profile_path(cls, user_id: str) -> Path:
        return cls._user_dir(user_id) / "profile.json"

    @classmethod
    def _prefs_path(cls, user_id: str) -> Path:
        return cls._user_dir(user_id) / "preferences.json"

    @classmethod
    def _email_index_path(cls) -> Path:
        return DATA_DIR / "email_index.json"

    # ── Email index ───────────────────────────────────────────

    @classmethod
    def _read_email_index(cls) -> dict:
        p = cls._email_index_path()
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    @classmethod
    def _write_email_index(cls, index: dict) -> None:
        _atomic_write(cls._email_index_path(), index)

    # ── CRUD ──────────────────────────────────────────────────

    @classmethod
    def create_user(cls, email: str, password: str, admin: bool = False) -> dict:
        """Create a new user. Returns the profile dict. Raises ValueError if email taken."""
        import uuid as _uuid

        # Lock ordering: email_index first, then user
        ei_lock = _get_lock("email_index")
        with ei_lock:
            index = cls._read_email_index()
            if email.lower() in index:
                raise ValueError(f"Email already registered: {email}")

            user_id = str(_uuid.uuid4())
            profile = {
                "id": user_id,
                "email": email.lower(),
                "password_hash": generate_password_hash(password),
                "admin": admin,
                "suspended": False,
                "verified": False,
                "created_at": datetime.utcnow().isoformat(),
                "verify_token_hash": None,
                "verify_token_expires": None,
                "reset_token_hash": None,
                "reset_token_expires": None,
                "failed_logins": 0,
                "locked_until": None,
            }

            user_lock = _get_lock(user_id)
            with user_lock:
                _atomic_write(cls._profile_path(user_id), profile)
                # Create default tasks file
                tasks_file = cls._user_dir(user_id) / "tasks.md"
                if not tasks_file.exists():
                    tasks_file.write_text("# Tasks\n\n## Inbox\n\n", encoding="utf-8")
                # Default prefs
                prefs = {
                    "theme": "system",
                    "font_size": "medium",
                    "sort_col": "due",
                    "sort_dir": "asc",
                }
                _atomic_write(cls._prefs_path(user_id), prefs)

            index[email.lower()] = user_id
            cls._write_email_index(index)

        return profile

    @classmethod
    def get_user(cls, user_id: str) -> Optional[dict]:
        """Return profile dict or None."""
        p = cls._profile_path(user_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    @classmethod
    def get_by_email(cls, email: str) -> Optional[dict]:
        """Return profile dict by email or None."""
        index = cls._read_email_index()
        user_id = index.get(email.lower())
        if not user_id:
            return None
        return cls.get_user(user_id)

    @classmethod
    def save_profile(cls, profile: dict) -> None:
        """Save (overwrite) a profile dict."""
        user_id = profile["id"]
        with _get_lock(user_id):
            _atomic_write(cls._profile_path(user_id), profile)

    @classmethod
    def list_users(cls) -> list:
        """Return list of all profile dicts."""
        users = []
        if not DATA_DIR.exists():
            return users
        for d in DATA_DIR.iterdir():
            if d.is_dir():
                p = d / "profile.json"
                if p.exists():
                    try:
                        users.append(json.loads(p.read_text(encoding="utf-8")))
                    except (json.JSONDecodeError, OSError):
                        pass
        return users

    # ── Auth ──────────────────────────────────────────────────

    @classmethod
    def authenticate(cls, email: str, password: str) -> Optional[dict]:
        """Return profile if credentials valid, else None.

        Does NOT check suspended/verified — caller decides what to do.
        Records failed logins and enforces lockout.
        """
        user = cls.get_by_email(email)
        if not user:
            return None

        if cls.is_locked(user):
            return None

        if check_password_hash(user["password_hash"], password):
            cls.clear_failed_logins(user["id"])
            return cls.get_user(user["id"])  # re-read after write
        else:
            cls.record_failed_login(user["id"])
            return None

    @classmethod
    def record_failed_login(cls, user_id: str) -> None:
        with _get_lock(user_id):
            user = cls.get_user(user_id)
            if not user:
                return
            user["failed_logins"] = user.get("failed_logins", 0) + 1
            if user["failed_logins"] >= _MAX_FAILED_LOGINS:
                locked_until = datetime.utcnow() + timedelta(minutes=_LOCKOUT_MINUTES)
                user["locked_until"] = locked_until.isoformat()
            _atomic_write(cls._profile_path(user_id), user)

    @classmethod
    def clear_failed_logins(cls, user_id: str) -> None:
        with _get_lock(user_id):
            user = cls.get_user(user_id)
            if not user:
                return
            user["failed_logins"] = 0
            user["locked_until"] = None
            _atomic_write(cls._profile_path(user_id), user)

    @classmethod
    def is_locked(cls, user: dict) -> bool:
        locked_until = user.get("locked_until")
        if not locked_until:
            return False
        return datetime.utcnow() < datetime.fromisoformat(locked_until)

    # ── Verify token ──────────────────────────────────────────

    @classmethod
    def create_verify_token(cls, user_id: str) -> str:
        """Create and store a verification token. Returns the raw token."""
        token = secrets.token_urlsafe(32)
        with _get_lock(user_id):
            user = cls.get_user(user_id)
            if not user:
                raise ValueError("User not found")
            expires = datetime.utcnow() + timedelta(hours=24)
            user["verify_token_hash"] = _sha256(token)
            user["verify_token_expires"] = expires.isoformat()
            _atomic_write(cls._profile_path(user_id), user)
        return token

    @classmethod
    def consume_verify_token(cls, token: str) -> Optional[dict]:
        """Verify token, mark account verified, clear token. Returns user or None."""
        token_hash = _sha256(token)
        # Find user with matching token hash
        for user in cls.list_users():
            if user.get("verify_token_hash") == token_hash:
                expires_str = user.get("verify_token_expires")
                if not expires_str:
                    return None
                if datetime.utcnow() > datetime.fromisoformat(expires_str):
                    return None  # expired
                with _get_lock(user["id"]):
                    user = cls.get_user(user["id"])
                    if not user or user.get("verify_token_hash") != token_hash:
                        return None
                    user["verified"] = True
                    user["verify_token_hash"] = None
                    user["verify_token_expires"] = None
                    _atomic_write(cls._profile_path(user["id"]), user)
                return user
        return None

    # ── Reset token ───────────────────────────────────────────

    @classmethod
    def create_reset_token(cls, user_id: str) -> str:
        """Create and store a password reset token. Returns the raw token."""
        token = secrets.token_urlsafe(32)
        with _get_lock(user_id):
            user = cls.get_user(user_id)
            if not user:
                raise ValueError("User not found")
            expires = datetime.utcnow() + timedelta(hours=1)
            user["reset_token_hash"] = _sha256(token)
            user["reset_token_expires"] = expires.isoformat()
            _atomic_write(cls._profile_path(user_id), user)
        return token

    @classmethod
    def consume_reset_token(cls, token: str) -> Optional[dict]:
        """Validate reset token. Returns user if valid (does NOT clear token yet)."""
        token_hash = _sha256(token)
        for user in cls.list_users():
            if user.get("reset_token_hash") == token_hash:
                expires_str = user.get("reset_token_expires")
                if not expires_str:
                    return None
                if datetime.utcnow() > datetime.fromisoformat(expires_str):
                    return None  # expired
                return user
        return None

    @classmethod
    def _clear_reset_token(cls, user_id: str) -> None:
        with _get_lock(user_id):
            user = cls.get_user(user_id)
            if not user:
                return
            user["reset_token_hash"] = None
            user["reset_token_expires"] = None
            _atomic_write(cls._profile_path(user_id), user)

    # ── Preferences ───────────────────────────────────────────

    @classmethod
    def get_prefs(cls, user_id: str) -> dict:
        p = cls._prefs_path(user_id)
        defaults = {"theme": "system", "font_size": "medium", "sort_col": "due", "sort_dir": "asc"}
        if not p.exists():
            return defaults
        try:
            prefs = json.loads(p.read_text(encoding="utf-8"))
            # Merge with defaults for missing keys
            for k, v in defaults.items():
                prefs.setdefault(k, v)
            return prefs
        except (json.JSONDecodeError, OSError):
            return defaults

    @classmethod
    def save_prefs(cls, user_id: str, prefs: dict) -> None:
        with _get_lock(user_id):
            _atomic_write(cls._prefs_path(user_id), prefs)

    # ── Admin operations ──────────────────────────────────────

    @classmethod
    def suspend_user(cls, user_id: str, suspended: bool = True) -> None:
        with _get_lock(user_id):
            user = cls.get_user(user_id)
            if not user:
                raise ValueError("User not found")
            user["suspended"] = suspended
            _atomic_write(cls._profile_path(user_id), user)

    @classmethod
    def delete_user(cls, user_id: str) -> None:
        """Delete user profile, prefs, and remove from email index."""
        user = cls.get_user(user_id)
        if not user:
            return

        ei_lock = _get_lock("email_index")
        with ei_lock:
            index = cls._read_email_index()
            email = user.get("email", "")
            index.pop(email.lower(), None)
            cls._write_email_index(index)

            user_lock = _get_lock(user_id)
            with user_lock:
                profile_path = cls._profile_path(user_id)
                prefs_path = cls._prefs_path(user_id)
                if profile_path.exists():
                    profile_path.unlink()
                if prefs_path.exists():
                    prefs_path.unlink()

    @classmethod
    def change_email(cls, user_id: str, new_email: str) -> None:
        """Change user email. Lock ordering: email_index first, then user."""
        new_email = new_email.lower()
        ei_lock = _get_lock("email_index")
        with ei_lock:
            index = cls._read_email_index()
            if new_email in index and index[new_email] != user_id:
                raise ValueError("Email already in use")

            user_lock = _get_lock(user_id)
            with user_lock:
                user = cls.get_user(user_id)
                if not user:
                    raise ValueError("User not found")
                old_email = user.get("email", "").lower()

                # Update index
                index.pop(old_email, None)
                index[new_email] = user_id
                cls._write_email_index(index)

                # Update profile
                user["email"] = new_email
                user["verified"] = False  # Re-verify new email
                _atomic_write(cls._profile_path(user_id), user)

    @classmethod
    def change_password(cls, user_id: str, new_password: str) -> None:
        with _get_lock(user_id):
            user = cls.get_user(user_id)
            if not user:
                raise ValueError("User not found")
            user["password_hash"] = generate_password_hash(new_password)
            user["reset_token_hash"] = None
            user["reset_token_expires"] = None
            _atomic_write(cls._profile_path(user_id), user)

    @classmethod
    def toggle_admin(cls, user_id: str) -> bool:
        """Toggle admin status. Returns new admin state."""
        with _get_lock(user_id):
            user = cls.get_user(user_id)
            if not user:
                raise ValueError("User not found")
            user["admin"] = not user.get("admin", False)
            _atomic_write(cls._profile_path(user_id), user)
            return user["admin"]

    @classmethod
    def get_task_count(cls, user_id: str) -> int:
        """Return number of incomplete tasks for a user."""
        tasks_file = DATA_DIR / user_id / "tasks.md"
        if not tasks_file.exists():
            return 0
        import re as _re
        count = 0
        for line in tasks_file.read_text(encoding="utf-8").splitlines():
            if _re.match(r'^\s*- \[ \]', line):
                count += 1
        return count
