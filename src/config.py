#!/usr/bin/env python3
"""
Load config.json from the repo root.

Usage in other modules:
    from config import cfg_get, cfg_bool, cfg_int
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_FILE = REPO_ROOT / "config.json"

_c: dict = {}
_mtime: float = 0.0


def _load() -> None:
    """Load config.json, raising SystemExit with a clear message on any failure."""
    global _c, _mtime
    if not _CONFIG_FILE.exists():
        sys.exit(
            f"[config] config.json not found at {_CONFIG_FILE}\n"
            f"Copy config.json.example to config.json and fill in your settings."
        )
    try:
        text = _CONFIG_FILE.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"[config] Could not read config.json: {e}")
    try:
        _c = json.loads(text)
    except json.JSONDecodeError as e:
        sys.exit(f"[config] config.json is not valid JSON: {e}")
    _mtime = _CONFIG_FILE.stat().st_mtime


def _reload_if_changed() -> None:
    global _mtime
    try:
        mtime = _CONFIG_FILE.stat().st_mtime
    except FileNotFoundError:
        sys.exit(f"[config] config.json was deleted while running — cannot continue.")
    if mtime != _mtime:
        _load()


_load()


def cfg_get(section: str, key: str, default: str = "") -> str:
    _reload_if_changed()
    v = _c.get(section, {}).get(key)
    return str(v) if v is not None else default


def cfg_int(section: str, key: str, default: int = 0) -> int:
    _reload_if_changed()
    v = _c.get(section, {}).get(key)
    return int(v) if v is not None else default


def cfg_bool(section: str, key: str, default: bool = False) -> bool:
    _reload_if_changed()
    v = _c.get(section, {}).get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() not in ("false", "0", "no", "")


