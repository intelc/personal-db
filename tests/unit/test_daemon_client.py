from unittest.mock import patch

import pytest
import requests

from personal_db.services.daemon import client as dc


def test_default_base_url_is_loopback_8765():
    assert dc.base_url() == "http://127.0.0.1:8765"


def test_base_url_respects_env(monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_DAEMON_URL", "http://127.0.0.1:9000")
    assert dc.base_url() == "http://127.0.0.1:9000"


def test_sync_one_translates_connection_error():
    with patch.object(dc.requests, "post", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(dc.DaemonUnreachable) as ei:
            dc.sync_one("imessage")
        assert "daemon not running" in str(ei.value).lower()


def test_sync_one_returns_parsed_json_on_success():
    class FakeResp:
        status_code = 200
        def json(self):
            return {"ok": True, "tracker": "imessage"}
        def raise_for_status(self):
            return None
    with patch.object(dc.requests, "post", return_value=FakeResp()):
        out = dc.sync_one("imessage")
    assert out == {"ok": True, "tracker": "imessage"}


def test_sync_one_raises_daemon_error_on_5xx():
    class FakeResp:
        status_code = 500
        text = "boom"
        def raise_for_status(self):
            raise requests.HTTPError("500", response=self)
    with patch.object(dc.requests, "post", return_value=FakeResp()):
        with pytest.raises(dc.DaemonError):
            dc.sync_one("imessage")


def test_health_returns_dict_or_unreachable():
    with patch.object(dc.requests, "get", side_effect=requests.ConnectionError("nope")):
        with pytest.raises(dc.DaemonUnreachable):
            dc.health()


def test_health_raises_daemon_error_on_5xx():
    class FakeResp:
        status_code = 500
        text = "boom"
        def raise_for_status(self):
            raise requests.HTTPError("500", response=self)
        def json(self):
            return {}
    with patch.object(dc.requests, "get", return_value=FakeResp()):
        with pytest.raises(dc.DaemonError):
            dc.health()
