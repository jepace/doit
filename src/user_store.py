#!/usr/bin/env python3
"""
User store — all data under data/{uuid}/:
  profile.json      auth fields (email, password_hash, tokens, etc.)
  preferences.json  display/UX preferences
  tasks.md          per-user tasks
"""

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR  = REPO_ROOT / "data"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_VALID_THEMES    = {"light", "dark", "system"}
_VALID_FONTSIZES = {"small", "medium", "large"}
_VALID_SORT_COLS = {"due", "priority", "context", "description", "start"}
_VALID_SORT_DIRS = {"asc", "desc"}

try:
    import fcntl as _fcntl

    @contextlib.contextmanager
    def _get_lock(key: str):
        """Exclusive cross-process lock via fcntl.flock (Linux, macOS, FreeBSD, BSDs)."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = DATA_DIR / f".{key}.lock"
        with open(lock_path, "w") as lf:
            _fcntl.flock(lf, _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(lf, _fcntl.LOCK_UN)

except ImportError:
    # Windows: fcntl unavailable. Thread-safe but not multi-process safe.
    # Run with a single WSGI worker on Windows.
    import threading as _threading
    _locks: dict[str, "_threading.Lock"] = {}
    _locks_mu = _threading.Lock()

    @contextlib.contextmanager
    def _get_lock(key: str):
        """Thread-local lock (Windows fallback — not safe with multiple workers)."""
        with _locks_mu:
            if key not in _locks:
                _locks[key] = _threading.Lock()
            lock = _locks[key]
        with lock:
            yield


def get_user_lock(user_id: str):
    """Public: per-user lock, shared across profile and tasks file writes."""
    return _get_lock(user_id)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _user_dir(user_id: str) -> Path:
    return DATA_DIR / user_id

def _profile_path(user_id: str) -> Path:
    return _user_dir(user_id) / "profile.json"

def _prefs_path(user_id: str) -> Path:
    return _user_dir(user_id) / "preferences.json"

def _email_index_path() -> Path:
    return DATA_DIR / "email_index.json"

def _token_index_path() -> Path:
    return DATA_DIR / "token_index.json"


# ---------------------------------------------------------------------------
# Low-level I/O (atomic writes)
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_email_index() -> dict:
    return _read_json(_email_index_path()) or {}


def _save_email_index(index: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(_email_index_path(), index)


def _utcnow() -> datetime:
    """Timezone-aware-safe UTC now, returned as a naive datetime for storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load_token_index() -> dict:
    return _read_json(_token_index_path()) or {}


def _save_token_index(index: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(_token_index_path(), index)


def _token_hash(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def _default_prefs() -> dict:
    return {
        "theme":     "system",
        "font_size": "medium",
        "sort_col":  "due",
        "sort_dir":  "asc",
        "sort_col2": "",
        "sort_dir2": "asc",
        "sort_col3": "",
        "sort_dir3": "asc",
    }


# ---------------------------------------------------------------------------
# UserStore
# ---------------------------------------------------------------------------

# Pre-computed hash used to equalize authenticate() timing when the user doesn't exist.
_DUMMY_HASH = generate_password_hash("dummy-for-timing-equalization")


class UserStore:
    """Stateless — all methods read/write files under data/{uuid}/."""

    # ── Create / read / write ─────────────────────────────────────────────

    @classmethod
    def create_user(cls, email: str, password: str) -> dict:
        """Create a new user. Returns the merged user dict. Raises ValueError on failure."""
        email = email.strip().lower()
        if not _EMAIL_RE.match(email):
            raise ValueError("Invalid email address.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        with _get_lock("email_index"):
            index = _load_email_index()
            if email in index:
                raise ValueError("An account with that email already exists.")

            user_id = str(uuid.uuid4())
            user_dir = _user_dir(user_id)
            user_dir.mkdir(parents=True, exist_ok=True)

            profile = {
                "id":                   user_id,
                "email":                email,
                "password_hash":        generate_password_hash(password),
                "admin":                False,
                "suspended":            False,
                "verified":             False,
                "created_at":           _utcnow().isoformat(),
                "verify_token_hash":    None,
                "verify_token_expires": None,
                "reset_token_hash":     None,
                "reset_token_expires":  None,
                "failed_logins":        0,
                "locked_until":         None,
            }
            _write_json(_profile_path(user_id), profile)
            _write_json(_prefs_path(user_id), _default_prefs())
            (user_dir / "tasks.md").write_text("# Tasks\n\n## Inbox\n\n", encoding="utf-8")

            index[email] = user_id
            _save_email_index(index)

        return cls._merge(profile, _default_prefs())

    @classmethod
    def get_user(cls, user_id: str) -> dict | None:
        profile = _read_json(_profile_path(user_id))
        if profile is None:
            return None
        prefs = _read_json(_prefs_path(user_id)) or _default_prefs()
        return cls._merge(profile, prefs)

    @classmethod
    def get_by_email(cls, email: str) -> dict | None:
        user_id = _load_email_index().get(email.strip().lower())
        return cls.get_user(user_id) if user_id else None

    @classmethod
    def save_profile(cls, profile: dict) -> None:
        """Write profile fields (everything except prefs)."""
        lock = _get_lock(profile["id"])
        with lock:
            _write_json(_profile_path(profile["id"]), profile)

    @classmethod
    def _merge(cls, profile: dict, prefs: dict) -> dict:
        """Return a combined dict used by the rest of the app."""
        merged = dict(profile)
        # Merge with defaults so new pref keys are always present
        full_prefs = _default_prefs()
        full_prefs.update({k: v for k, v in prefs.items() if k in full_prefs})
        merged["prefs"] = full_prefs
        return merged

    @classmethod
    def list_users(cls) -> list[dict]:
        if not DATA_DIR.exists():
            return []
        result = []
        for d in DATA_DIR.iterdir():
            if not d.is_dir():
                continue
            profile = _read_json(d / "profile.json")
            if profile is None:
                continue
            prefs = _read_json(d / "preferences.json") or _default_prefs()
            result.append(cls._merge(profile, prefs))
        return result

    # ── Authentication ────────────────────────────────────────────────────

    @classmethod
    def authenticate(cls, email: str, password: str) -> tuple[dict | None, str]:
        """Returns (user, "") on success or (None, error) on failure."""
        user = cls.get_by_email(email)
        if user is None:
            check_password_hash(_DUMMY_HASH, password)  # equalize timing
            return None, "Invalid email or password."
        if user.get("suspended"):
            return None, "This account has been suspended."
        if cls.is_locked(user["id"]):
            return None, "Account temporarily locked. Try again later."
        if not user.get("verified"):
            return None, "Please verify your email address before signing in."
        if not check_password_hash(user["password_hash"], password):
            cls.record_failed_login(user["id"])
            return None, "Invalid email or password."
        cls.clear_failed_logins(user["id"])
        return user, ""

    @classmethod
    def record_failed_login(cls, user_id: str) -> None:
        with _get_lock(user_id):
            p = _read_json(_profile_path(user_id))
            if p is None:
                return
            p["failed_logins"] = p.get("failed_logins", 0) + 1
            if p["failed_logins"] >= 5:
                p["locked_until"] = (_utcnow() + timedelta(minutes=15)).isoformat()
            _write_json(_profile_path(user_id), p)

    @classmethod
    def clear_failed_logins(cls, user_id: str) -> None:
        with _get_lock(user_id):
            p = _read_json(_profile_path(user_id))
            if p is None:
                return
            p["failed_logins"] = 0
            p["locked_until"]  = None
            _write_json(_profile_path(user_id), p)

    @classmethod
    def is_locked(cls, user_id: str) -> bool:
        p = _read_json(_profile_path(user_id))
        if not p:
            return False
        locked_until = p.get("locked_until")
        if not locked_until:
            return False
        try:
            if _utcnow() >= datetime.fromisoformat(locked_until):
                cls.clear_failed_logins(user_id)
                return False
        except ValueError:
            return False
        return True

    # ── Tokens ────────────────────────────────────────────────────────────

    @classmethod
    def create_verify_token(cls, user_id: str) -> str:
        plain = secrets.token_urlsafe(32)
        h = _token_hash(plain)
        old_h = None
        with _get_lock(user_id):
            p = _read_json(_profile_path(user_id))
            if p is None:
                raise ValueError("User not found.")
            old_h = p.get("verify_token_hash")
            p["verify_token_hash"]    = h
            p["verify_token_expires"] = (_utcnow() + timedelta(hours=24)).isoformat()
            _write_json(_profile_path(user_id), p)
        with _get_lock("token_index"):
            idx = _load_token_index()
            if old_h:
                idx.pop(old_h, None)
            idx[h] = user_id
            _save_token_index(idx)
        return plain

    @classmethod
    def consume_verify_token(cls, plain_token: str) -> dict | None:
        h = _token_hash(plain_token)
        with _get_lock("token_index"):
            idx = _load_token_index()
            user_id = idx.get(h)
            if user_id is None:
                return None
            del idx[h]
            _save_token_index(idx)
        with _get_lock(user_id):
            p = _read_json(_profile_path(user_id))
            stored_h = p.get("verify_token_hash") or "" if p else ""
            if p is None or not secrets.compare_digest(stored_h, h):
                return None
            exp = p.get("verify_token_expires")
            try:
                if exp and _utcnow() > datetime.fromisoformat(exp):
                    return None
            except ValueError:
                return None
            p["verified"]             = True
            p["verify_token_hash"]    = None
            p["verify_token_expires"] = None
            _write_json(_profile_path(user_id), p)
        return cls.get_user(user_id)

    @classmethod
    def create_reset_token(cls, email: str) -> tuple[str, dict] | tuple[None, None]:
        user = cls.get_by_email(email)
        if user is None:
            return None, None
        plain = secrets.token_urlsafe(32)
        h = _token_hash(plain)
        old_h = None
        with _get_lock(user["id"]):
            p = _read_json(_profile_path(user["id"]))
            if p is None:
                return None, None
            old_h = p.get("reset_token_hash")
            p["reset_token_hash"]    = h
            p["reset_token_expires"] = (_utcnow() + timedelta(hours=1)).isoformat()
            _write_json(_profile_path(user["id"]), p)
        with _get_lock("token_index"):
            idx = _load_token_index()
            if old_h:
                idx.pop(old_h, None)
            idx[h] = user["id"]
            _save_token_index(idx)
        return plain, user

    @classmethod
    def consume_reset_token(cls, plain_token: str, new_password: str) -> bool:
        if len(new_password) < 8:
            return False
        h = _token_hash(plain_token)
        with _get_lock("token_index"):
            idx = _load_token_index()
            user_id = idx.get(h)
            if user_id is None:
                return False
            del idx[h]
            _save_token_index(idx)
        with _get_lock(user_id):
            p = _read_json(_profile_path(user_id))
            stored_h = p.get("reset_token_hash") or "" if p else ""
            if p is None or not secrets.compare_digest(stored_h, h):
                return False
            exp = p.get("reset_token_expires")
            try:
                if exp and _utcnow() > datetime.fromisoformat(exp):
                    return False
            except ValueError:
                return False
            p["password_hash"]       = generate_password_hash(new_password)
            p["reset_token_hash"]    = None
            p["reset_token_expires"] = None
            p["failed_logins"]       = 0
            p["locked_until"]        = None
            _write_json(_profile_path(user_id), p)
        return True

    # ── Preferences ───────────────────────────────────────────────────────

    @classmethod
    def get_prefs(cls, user_id: str) -> dict:
        prefs = _read_json(_prefs_path(user_id)) or {}
        merged = _default_prefs()
        merged.update({k: v for k, v in prefs.items() if k in merged})
        return merged

    @classmethod
    def save_prefs(cls, user_id: str, theme: str, font_size: str,
                   sort_col: str, sort_dir: str,
                   sort_col2: str = "", sort_dir2: str = "asc",
                   sort_col3: str = "", sort_dir3: str = "asc") -> None:
        def _valid_col(v): return v if v in _VALID_SORT_COLS else ""
        prefs = {
            "theme":     theme     if theme     in _VALID_THEMES    else "system",
            "font_size": font_size if font_size in _VALID_FONTSIZES else "medium",
            "sort_col":  sort_col  if sort_col  in _VALID_SORT_COLS else "due",
            "sort_dir":  sort_dir  if sort_dir  in _VALID_SORT_DIRS else "asc",
            "sort_col2": _valid_col(sort_col2),
            "sort_dir2": sort_dir2 if sort_dir2 in _VALID_SORT_DIRS else "asc",
            "sort_col3": _valid_col(sort_col3),
            "sort_dir3": sort_dir3 if sort_dir3 in _VALID_SORT_DIRS else "asc",
        }
        with _get_lock(user_id):
            _write_json(_prefs_path(user_id), prefs)

    # ── Account management ────────────────────────────────────────────────

    @classmethod
    def suspend_user(cls, user_id: str, suspended: bool) -> None:
        with _get_lock(user_id):
            p = _read_json(_profile_path(user_id))
            if p is None:
                return
            p["suspended"] = suspended
            _write_json(_profile_path(user_id), p)

    @classmethod
    def delete_user(cls, user_id: str) -> None:
        # Acquire email_index first — same ordering as change_email — to prevent deadlock.
        with _get_lock("email_index"):
            with _get_lock(user_id):
                # Read profile inside the lock so we see the current email.
                p = _read_json(_profile_path(user_id))
                email = p.get("email") if p else None
                if _user_dir(user_id).exists():
                    shutil.rmtree(_user_dir(user_id))
            if email:
                index = _load_email_index()
                index.pop(email, None)
                _save_email_index(index)

    @classmethod
    def change_email(cls, user_id: str, new_email: str) -> None:
        new_email = new_email.strip().lower()
        if not _EMAIL_RE.match(new_email):
            raise ValueError("Invalid email address.")
        with _get_lock("email_index"):
            index = _load_email_index()
            existing = index.get(new_email)
            if existing and existing != user_id:
                raise ValueError("That email address is already in use.")
            with _get_lock(user_id):
                p = _read_json(_profile_path(user_id))
                if p is None:
                    raise ValueError("User not found.")
                old_email = p["email"]
                p["email"]                = new_email
                p["verified"]             = False
                p["verify_token_hash"]    = None
                p["verify_token_expires"] = None
                _write_json(_profile_path(user_id), p)
            index.pop(old_email, None)
            index[new_email] = user_id
            _save_email_index(index)

    @classmethod
    def change_password(cls, user_id: str, current_password: str,
                        new_password: str) -> tuple[bool, str]:
        if len(new_password) < 8:
            return False, "Password must be at least 8 characters."
        with _get_lock(user_id):
            p = _read_json(_profile_path(user_id))
            if p is None:
                return False, "User not found."
            if not check_password_hash(p["password_hash"], current_password):
                return False, "Current password is incorrect."
            p["password_hash"] = generate_password_hash(new_password)
            _write_json(_profile_path(user_id), p)
        return True, ""

    @classmethod
    def toggle_admin(cls, user_id: str) -> None:
        with _get_lock(user_id):
            p = _read_json(_profile_path(user_id))
            if p is None:
                return
            p["admin"] = not p.get("admin", False)
            _write_json(_profile_path(user_id), p)

    # ── Helpers ───────────────────────────────────────────────────────────

    @classmethod
    def get_task_count(cls, user_id: str) -> int:
        tasks_file = _user_dir(user_id) / "tasks.md"
        if not tasks_file.exists():
            return 0
        try:
            content = tasks_file.read_text(encoding="utf-8")
            return len(re.findall(r"^\s*- \[[x ]\]", content, re.MULTILINE))
        except OSError:
            return 0
