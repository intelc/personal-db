import urllib.request
from unittest.mock import MagicMock, patch

from personal_db.config import Config
from personal_db.oauth import OAuthFlow, exchange_code, load_token, save_token


def test_save_and_load_token(tmp_root):
    cfg = Config(root=tmp_root)
    save_token(cfg, "whoop", {"access_token": "a", "refresh_token": "r", "expires_at": 9999999999})
    t = load_token(cfg, "whoop")
    assert t["access_token"] == "a"


def test_callback_captures_code():
    """Spin up the callback server, hit it with a code, assert capture."""
    flow = OAuthFlow(state="xyz", port=0)  # port=0 -> ephemeral
    flow.start()
    try:
        url = f"http://127.0.0.1:{flow.port}/callback?state=xyz&code=abc123"
        urllib.request.urlopen(url, timeout=2).read()
        code = flow.wait_for_code(timeout_s=2)
        assert code == "abc123"
    finally:
        flow.shutdown()


def test_exchange_code_posts_to_token_url_and_returns_token():
    with patch("personal_db.oauth.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 3600,
            },
        )
        mock_post.return_value.raise_for_status = MagicMock()
        token = exchange_code(
            token_url="https://example.com/token",
            client_id="CID",
            client_secret="CS",
            code="ABC",
            redirect_uri="http://127.0.0.1:8080/callback",
        )
    assert token["access_token"] == "AT"
    assert token["refresh_token"] == "RT"
    assert "expires_at" in token  # we add this for refresh_if_needed
    args, kwargs = mock_post.call_args
    assert args[0] == "https://example.com/token"
    data = kwargs["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "ABC"
    assert data["redirect_uri"] == "http://127.0.0.1:8080/callback"
    assert data["client_id"] == "CID"
    assert data["client_secret"] == "CS"
