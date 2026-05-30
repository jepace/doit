"""
Tests for mailer.py — all network calls are mocked.
"""

import sys
import json
import io
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import mailer


def _mock_response(status: int):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestSendEmail:
    def test_returns_false_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda *a, **kw: "")
        assert mailer.send_email("a@b.com", "s", "body") is False

    def test_returns_true_on_200(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        with patch("urllib.request.urlopen", return_value=_mock_response(200)):
            assert mailer.send_email("a@b.com", "s", "body") is True

    def test_returns_true_on_201(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        with patch("urllib.request.urlopen", return_value=_mock_response(201)):
            assert mailer.send_email("a@b.com", "s", "body") is True

    def test_returns_false_on_non_2xx(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        with patch("urllib.request.urlopen", return_value=_mock_response(400)):
            assert mailer.send_email("a@b.com", "s", "body") is False

    def test_returns_false_on_network_error(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            assert mailer.send_email("a@b.com", "s", "body") is False

    def test_sends_html_when_provided(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_response(200)
        with patch("urllib.request.urlopen", fake_urlopen):
            mailer.send_email("a@b.com", "subj", "text", "<b>html</b>")
        assert captured["body"]["html"] == "<b>html</b>"
        assert captured["body"]["text"] == "text"

    def test_omits_html_when_not_provided(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_response(200)
        with patch("urllib.request.urlopen", fake_urlopen):
            mailer.send_email("a@b.com", "subj", "text only")
        assert "html" not in captured["body"]


class TestSendVerificationEmail:
    def test_contains_verify_url(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_response(200)
        with patch("urllib.request.urlopen", fake_urlopen):
            mailer.send_verification_email("user@example.com", "tok123", "https://example.com")
        assert "https://example.com/auth/verify/tok123" in captured["body"]["text"]
        assert "https://example.com/auth/verify/tok123" in captured["body"]["html"]

    def test_strips_trailing_slash_from_base_url(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_response(200)
        with patch("urllib.request.urlopen", fake_urlopen):
            mailer.send_verification_email("user@example.com", "tok", "https://example.com/")
        assert "https://example.com/auth/verify/tok" in captured["body"]["text"]
        assert "//auth" not in captured["body"]["text"]


class TestSendResetEmail:
    def test_contains_reset_url(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_response(200)
        with patch("urllib.request.urlopen", fake_urlopen):
            mailer.send_reset_email("user@example.com", "resettok", "https://example.com")
        assert "https://example.com/auth/reset/resettok" in captured["body"]["text"]

    def test_returns_false_on_failure(self, monkeypatch):
        monkeypatch.setattr(mailer, "cfg_get", lambda s, k, *a: "key" if k == "resend_api_key" else "from@x.com")
        with patch("urllib.request.urlopen", side_effect=OSError("fail")):
            assert mailer.send_reset_email("u@e.com", "tok", "https://x.com") is False
