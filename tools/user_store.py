#!/usr/bin/env python3
"""
User store for multi-user authentication.
All user data lives under data/ in the repo root.
"""

import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR  = REPO_ROOT / "data"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_VALID_THEMES     = {"light", "dark", "system"}
_VALID_FONTSIZES  = {"small", "medium", "large"}
_VALID_SORT_COLS  = {"due", "priority", "context", "description", "start"}
_VALID_SORT_DIRS  = {"asc", "desc"}


def _default_prefs() -> dict:
    return {
        "theme":          "system",
        "font_size":      "medium",
        "sort_col":       "due",
        "sort_dir":       "asc",
    }

# Per-user-id locks for atomic writes
_user_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(user_id: str) -> threading.Lock:
    with _locks_lock:
        if user_id not in _user_locks:
            _user_locks[user_id] = threading.Lock()
        return _user_locks[user_id]


def _email_index_path() -> Path:
    return DATA_DIR / "email_index.json"


def _users_dir() -> Path:
    return DATA_DIR / "users"


def _user_path(user_id: str) -> Path:
    return _users_dir() / f"{user_id}.json"


def _user_data_dir(user_id: str) -> Path:
    return DATA_DIR / user_id


def _load_email_index() -> dict:
    p = _email_index_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_email_index(index: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _email_index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
    os.replace(tmp, _email_index_path())


def _load_user_file(user_id: str) -> dict | None:
    p = _user_path(user_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_user_file(user_dict: dict) -> None:
    user_id = user_dict["id"]
    _users_dir().mkdir(parents=True, exist_ok=True)
    p = _user_path(user_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(user_dict, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _token_hash(plain_token: str) -> str:
    return hashlib.sha256(plain_token.encode()).hexdigest()


class UserStore:
    """Stateless class — all methods read/write files."""

    @classmethod
    def create_user(cls, email: str, password: str) -> dict:
        """
        Creates a new user. Returns the user dict.
        Raises ValueError on validation failures.
        """
        email = email.strip().lower()
        if not _EMAIL_RE.match(email):
            raise ValueError("Invalid email address.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        # Check duplicate
        index = _load_email_index()
        if email in index:
            raise ValueError("An account with that email already exists.")

        import uuid
        user_id = str(uuid.uuid4())

        user_dict = {
            "id": user_id,
            "email": email,
            "password_hash": generate_password_hash(password),
            "admin": False,
            "suspended": False,
            "verified": False,
            "created_at": datetime.utcnow().isoformat(),
            "verify_token_hash": None,
            "verify_token_expires": None,
            "reset_token_hash": None,
            "reset_token_expires": None,
            "failed_logins": 0,
            "locked_until": None,
            "prefs": _default_prefs(),
        }

        # Create per-user tasks.md
        user_data_dir = _user_data_dir(user_id)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        tasks_file = user_data_dir / "tasks.md"
        tasks_file.write_text(
            "# Tasks\n\n## Inbox\n\n",
            encoding="utf-8",
        )

        # Write user file
        _write_user_file(user_dict)

        # Update email index atomically
        with _get_lock("email_index"):
            index = _load_email_index()
            index[email] = user_id
            _save_email_index(index)

        return user_dict

    @classmethod
    def get_user(cls, user_id: str) -> dict | None:
        return _load_user_file(user_id)

    @classmethod
    def get_by_email(cls, email: str) -> dict | None:
        email = email.strip().lower()
        index = _load_email_index()
        user_id = index.get(email)
        if not user_id:
            return None
        return _load_user_file(user_id)

    @classmethod
    def save_user(cls, user_dict: dict) -> None:
        lock = _get_lock(user_dict["id"])
        with lock:
            _write_user_file(user_dict)

    @classmethod
    def authenticate(cls, email: str, password: str) -> tuple[dict | None, str]:
        """Returns (user_dict, "") on success or (None, error_message) on failure."""
        email = email.strip().lower()
        user = cls.get_by_email(email)
        if user is None:
            return None, "Invalid email or password."

        if user.get("suspended"):
            return None, "This account has been suspended."

        if cls.is_locked(user["id"]):
            return None, "Account is temporarily locked due to too many failed login attempts. Try again later."

        if not user.get("verified"):
            return None, "Please verify your email address before signing in."

        if not check_password_hash(user.get("password_hash", ""), password):
            cls.record_failed_login(user["id"])
            return None, "Invalid email or password."

        cls.clear_failed_logins(user["id"])
        return user, ""

    @classmethod
    def record_failed_login(cls, user_id: str) -> None:
        lock = _get_lock(user_id)
        with lock:
            user = _load_user_file(user_id)
            if user is None:
                return
            user["failed_logins"] = user.get("failed_logins", 0) + 1
            if user["failed_logins"] >= 5:
                user["locked_until"] = (datetime.utcnow() + timedelta(minutes=15)).isoformat()
            _write_user_file(user)

    @classmethod
    def clear_failed_logins(cls, user_id: str) -> None:
        lock = _get_lock(user_id)
        with lock:
            user = _load_user_file(user_id)
            if user is None:
                return
            user["failed_logins"] = 0
            user["locked_until"] = None
            _write_user_file(user)

    @classmethod
    def is_locked(cls, user_id: str) -> bool:
        user = _load_user_file(user_id)
        if user is None:
            return False
        locked_until = user.get("locked_until")
        if not locked_until:
            return False
        try:
            until_dt = datetime.fromisoformat(locked_until)
        except ValueError:
            return False
        if datetime.utcnow() >= until_dt:
            # Lock expired — clear it
            cls.clear_failed_logins(user_id)
            return False
        return True

    @classmethod
    def create_verify_token(cls, user_id: str) -> str:
        plain = secrets.token_urlsafe(32)
        expires = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        lock = _get_lock(user_id)
        with lock:
            user = _load_user_file(user_id)
            if user is None:
                raise ValueError("User not found.")
            user["verify_token_hash"] = _token_hash(plain)
            user["verify_token_expires"] = expires
            _write_user_file(user)
        return plain

    @classmethod
    def consume_verify_token(cls, plain_token: str) -> dict | None:
        token_h = _token_hash(plain_token)
        for user in cls.list_users():
            if user.get("verify_token_hash") == token_h:
                # Check expiry
                exp = user.get("verify_token_expires")
                if exp:
                    try:
                        if datetime.utcnow() > datetime.fromisoformat(exp):
                            return None
                    except ValueError:
                        return None
                lock = _get_lock(user["id"])
                with lock:
                    u = _load_user_file(user["id"])
                    if u is None:
                        return None
                    u["verified"] = True
                    u["verify_token_hash"] = None
                    u["verify_token_expires"] = None
                    _write_user_file(u)
                return u
        return None

    @classmethod
    def create_reset_token(cls, email: str) -> tuple[str, dict] | tuple[None, None]:
        user = cls.get_by_email(email)
        if user is None:
            return None, None
        plain = secrets.token_urlsafe(32)
        expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        lock = _get_lock(user["id"])
        with lock:
            u = _load_user_file(user["id"])
            if u is None:
                return None, None
            u["reset_token_hash"] = _token_hash(plain)
            u["reset_token_expires"] = expires
            _write_user_file(u)
        return plain, user

    @classmethod
    def consume_reset_token(cls, plain_token: str, new_password: str) -> bool:
        if len(new_password) < 8:
            return False
        token_h = _token_hash(plain_token)
        for user in cls.list_users():
            if user.get("reset_token_hash") == token_h:
                exp = user.get("reset_token_expires")
                if exp:
                    try:
                        if datetime.utcnow() > datetime.fromisoformat(exp):
                            return False
                    except ValueError:
                        return False
                lock = _get_lock(user["id"])
                with lock:
                    u = _load_user_file(user["id"])
                    if u is None:
                        return False
                    u["password_hash"] = generate_password_hash(new_password)
                    u["reset_token_hash"] = None
                    u["reset_token_expires"] = None
                    u["failed_logins"] = 0
                    u["locked_until"] = None
                    _write_user_file(u)
                return True
        return False

    @classmethod
    def list_users(cls) -> list[dict]:
        users_dir = _users_dir()
        if not users_dir.exists():
            return []
        result = []
        for p in users_dir.glob("*.json"):
            try:
                user = json.loads(p.read_text(encoding="utf-8"))
                result.append(user)
            except (json.JSONDecodeError, OSError):
                continue
        return result

    @classmethod
    def suspend_user(cls, user_id: str, suspended: bool) -> None:
        lock = _get_lock(user_id)
        with lock:
            user = _load_user_file(user_id)
            if user is None:
                return
            user["suspended"] = suspended
            _write_user_file(user)

    @classmethod
    def delete_user(cls, user_id: str) -> None:
        import shutil
        lock = _get_lock(user_id)
        with lock:
            user = _load_user_file(user_id)
            email = user.get("email") if user else None

            # Remove user file
            p = _user_path(user_id)
            if p.exists():
                p.unlink()

            # Remove user data directory
            user_data_dir = _user_data_dir(user_id)
            if user_data_dir.exists():
                shutil.rmtree(user_data_dir)

        # Remove from email index
        if email:
            with _get_lock("email_index"):
                index = _load_email_index()
                index.pop(email, None)
                _save_email_index(index)

    @classmethod
    def get_prefs(cls, user_id: str) -> dict:
        user = _load_user_file(user_id)
        if user is None:
            return _default_prefs()
        prefs = user.get("prefs") or {}
        # Merge with defaults so new keys are always present
        merged = _default_prefs()
        merged.update({k: v for k, v in prefs.items() if k in merged})
        return merged

    @classmethod
    def save_prefs(cls, user_id: str, theme: str, font_size: str,
                   sort_col: str, sort_dir: str) -> None:
        """Validate and persist user preferences."""
        theme     = theme     if theme     in _VALID_THEMES    else "system"
        font_size = font_size if font_size in _VALID_FONTSIZES else "medium"
        sort_col  = sort_col  if sort_col  in _VALID_SORT_COLS else "due"
        sort_dir  = sort_dir  if sort_dir  in _VALID_SORT_DIRS else "asc"

        lock = _get_lock(user_id)
        with lock:
            user = _load_user_file(user_id)
            if user is None:
                return
            user.setdefault("prefs", {})
            user["prefs"]["theme"]     = theme
            user["prefs"]["font_size"] = font_size
            user["prefs"]["sort_col"]  = sort_col
            user["prefs"]["sort_dir"]  = sort_dir
            _write_user_file(user)

    @classmethod
    def change_email(cls, user_id: str, new_email: str) -> None:
        """Change a user's email, updating the index atomically.
        Caller is responsible for setting verified=False and issuing a new
        verification token after calling this."""
        new_email = new_email.strip().lower()
        if not _EMAIL_RE.match(new_email):
            raise ValueError("Invalid email address.")

        # Check not already taken by another user
        index = _load_email_index()
        existing_id = index.get(new_email)
        if existing_id and existing_id != user_id:
            raise ValueError("That email address is already in use.")

        lock = _get_lock(user_id)
        with lock:
            user = _load_user_file(user_id)
            if user is None:
                raise ValueError("User not found.")
            old_email = user["email"]
            user["email"]    = new_email
            user["verified"] = False
            user["verify_token_hash"]    = None
            user["verify_token_expires"] = None
            _write_user_file(user)

        with _get_lock("email_index"):
            index = _load_email_index()
            index.pop(old_email, None)
            index[new_email] = user_id
            _save_email_index(index)

    @classmethod
    def change_password(cls, user_id: str, current_password: str,
                        new_password: str) -> tuple[bool, str]:
        """Verify current password then update to new one.
        Returns (success, error_message)."""
        if len(new_password) < 8:
            return False, "Password must be at least 8 characters."
        lock = _get_lock(user_id)
        with lock:
            user = _load_user_file(user_id)
            if user is None:
                return False, "User not found."
            if not check_password_hash(user["password_hash"], current_password):
                return False, "Current password is incorrect."
            user["password_hash"] = generate_password_hash(new_password)
            _write_user_file(user)
        return True, ""

    @classmethod
    def get_task_count(cls, user_id: str) -> int:
        tasks_file = _user_data_dir(user_id) / "tasks.md"
        if not tasks_file.exists():
            return 0
        try:
            content = tasks_file.read_text(encoding="utf-8")
            return len(re.findall(r"^\s*- \[[x ]\]", content, re.MULTILINE))
        except OSError:
            return 0
