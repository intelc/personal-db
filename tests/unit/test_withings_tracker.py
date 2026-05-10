import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

WITHINGS_DIR = Path(__file__).parent.parent.parent / "src" / "personal_db" / "templates" / "trackers" / "withings"


def _load_adapter_class():
    """Load WithingsAdapter the same way ensure_adapter_from_manifest does."""
    spec = importlib.util.spec_from_file_location(
        "withings_oauth_adapter_test", WITHINGS_DIR / "oauth_adapter.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.WithingsAdapter


@patch("requests.post")
def test_withings_adapter_exchange_code_unwraps_envelope(mock_post):
    cls = _load_adapter_class()
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "status": 0,
            "body": {
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 10800,
                "userid": 1234,
                "scope": "user.metrics",
                "token_type": "Bearer",
            },
        },
    )
    mock_post.return_value.raise_for_status = MagicMock()

    token = cls().exchange_code(
        token_url="ignored",
        client_id="CID",
        client_secret="CS",
        code="ABC",
        redirect_uri="http://localhost:9877/callback",
    )
    assert token["access_token"] == "AT"
    assert token["refresh_token"] == "RT"
    assert token["expires_in"] == 10800
    args, kwargs = mock_post.call_args
    assert args[0] == "https://wbsapi.withings.net/v2/oauth2"
    body = kwargs["data"]
    assert body["action"] == "requesttoken"
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "ABC"


@patch("requests.post")
def test_withings_adapter_refresh_token_unwraps_envelope(mock_post):
    cls = _load_adapter_class()
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "status": 0,
            "body": {
                "access_token": "AT2",
                "refresh_token": "RT2",
                "expires_in": 10800,
            },
        },
    )
    mock_post.return_value.raise_for_status = MagicMock()

    token = cls().refresh_token(
        token_url="ignored",
        client_id="CID",
        client_secret="CS",
        refresh_token="OLD_RT",
    )
    assert token["access_token"] == "AT2"
    body = mock_post.call_args.kwargs["data"]
    assert body["action"] == "requesttoken"
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "OLD_RT"


@patch("requests.post")
def test_withings_adapter_raises_on_nonzero_status(mock_post):
    cls = _load_adapter_class()
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"status": 401, "error": "invalid_token"},
    )
    mock_post.return_value.raise_for_status = MagicMock()

    with pytest.raises(RuntimeError, match="Withings token error"):
        cls().refresh_token(
            token_url="ignored",
            client_id="CID",
            client_secret="CS",
            refresh_token="X",
        )


@patch("requests.post")
def test_withings_adapter_raises_when_body_missing(mock_post):
    cls = _load_adapter_class()
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"status": 0},  # success status but no 'body' key
    )
    mock_post.return_value.raise_for_status = MagicMock()

    with pytest.raises(RuntimeError, match="missing 'body'"):
        cls().exchange_code(
            token_url="ignored",
            client_id="CID",
            client_secret="CS",
            code="ABC",
            redirect_uri="http://localhost:9877/callback",
        )
