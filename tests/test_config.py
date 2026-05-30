"""
Unit tests for config.py — cfg_get, cfg_bool, cfg_int.
"""

import json
import sys
from pathlib import Path

import pytest

def _write_config(tmp_path, data: dict) -> Path:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return cfg


@pytest.fixture(autouse=True)
def patch_config_file(tmp_path, monkeypatch):
    """Point config module at a fresh temp config.json before each test."""
    import config

    cfg_path = _write_config(tmp_path, {
        "server": {"host": "0.0.0.0", "port": 9000, "https": True, "secret": "abc"},
        "email":  {"resend_api_key": "key123"},
    })
    monkeypatch.setattr(config, "_CONFIG_FILE", cfg_path)
    # Force reload
    monkeypatch.setattr(config, "_mtime", 0.0)
    config._load()


class TestCfgGet:
    def test_existing_key(self):
        from config import cfg_get
        assert cfg_get("server", "host") == "0.0.0.0"

    def test_missing_key_returns_default(self):
        from config import cfg_get
        assert cfg_get("server", "nonexistent", "fallback") == "fallback"

    def test_missing_section_returns_default(self):
        from config import cfg_get
        assert cfg_get("missing_section", "key", "x") == "x"

    def test_empty_default(self):
        from config import cfg_get
        assert cfg_get("server", "missing") == ""

    def test_integer_value_coerced_to_str(self):
        from config import cfg_get
        assert cfg_get("server", "port") == "9000"

    def test_bool_value_coerced_to_str(self):
        from config import cfg_get
        result = cfg_get("server", "https")
        assert isinstance(result, str)


class TestCfgInt:
    def test_existing_int(self):
        from config import cfg_int
        assert cfg_int("server", "port") == 9000

    def test_missing_key_returns_default(self):
        from config import cfg_int
        assert cfg_int("server", "missing", 42) == 42

    def test_missing_section_returns_default(self):
        from config import cfg_int
        assert cfg_int("nope", "key", 7) == 7


class TestCfgBool:
    def test_true_value(self):
        from config import cfg_bool
        assert cfg_bool("server", "https") is True

    def test_missing_key_returns_default_false(self):
        from config import cfg_bool
        assert cfg_bool("server", "missing") is False

    def test_missing_key_returns_default_true(self):
        from config import cfg_bool
        assert cfg_bool("server", "missing", True) is True

    def test_missing_section_returns_default(self):
        from config import cfg_bool
        assert cfg_bool("nope", "key", False) is False

    def test_string_false_returns_false(self, tmp_path, monkeypatch):
        """L4: string "false" must not evaluate to True via bool()."""
        import config
        cfg_path = _write_config(tmp_path, {"server": {"https": "false"}})
        monkeypatch.setattr(config, "_CONFIG_FILE", cfg_path)
        monkeypatch.setattr(config, "_mtime", 0.0)
        config._load()
        assert config.cfg_bool("server", "https") is False

    def test_string_zero_returns_false(self, tmp_path, monkeypatch):
        import config
        cfg_path = _write_config(tmp_path, {"server": {"https": "0"}})
        monkeypatch.setattr(config, "_CONFIG_FILE", cfg_path)
        monkeypatch.setattr(config, "_mtime", 0.0)
        config._load()
        assert config.cfg_bool("server", "https") is False

    def test_string_true_returns_true(self, tmp_path, monkeypatch):
        import config
        cfg_path = _write_config(tmp_path, {"server": {"https": "true"}})
        monkeypatch.setattr(config, "_CONFIG_FILE", cfg_path)
        monkeypatch.setattr(config, "_mtime", 0.0)
        config._load()
        assert config.cfg_bool("server", "https") is True

    def test_json_bool_true_returns_true(self, tmp_path, monkeypatch):
        import config
        cfg_path = _write_config(tmp_path, {"server": {"https": True}})
        monkeypatch.setattr(config, "_CONFIG_FILE", cfg_path)
        monkeypatch.setattr(config, "_mtime", 0.0)
        config._load()
        assert config.cfg_bool("server", "https") is True


class TestReloadOnChange:
    def test_reload_detects_change(self, tmp_path, monkeypatch):
        import config

        cfg_path = _write_config(tmp_path, {"server": {"host": "before"}})
        monkeypatch.setattr(config, "_CONFIG_FILE", cfg_path)
        monkeypatch.setattr(config, "_mtime", 0.0)
        config._load()

        assert config.cfg_get("server", "host") == "before"

        # Write a new value and force stale mtime so reload triggers
        cfg_path.write_text(json.dumps({"server": {"host": "after"}}), encoding="utf-8")
        monkeypatch.setattr(config, "_mtime", 0.0)

        assert config.cfg_get("server", "host") == "after"
