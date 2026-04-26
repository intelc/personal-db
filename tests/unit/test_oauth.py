import urllib.request

from personal_db.config import Config
from personal_db.oauth import OAuthFlow, load_token, save_token


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
