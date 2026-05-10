import socket
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

from personal_db.config import Config
from personal_db.oauth import (
    OAuthFlow,
    exchange_code,
    load_token,
    save_token,
    start_web_oauth,
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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


# This test calls exchange_code WITHOUT a `provider` arg, so it routes
# through the default `_standard` sentinel → StandardAdapter. The
# requests.post mock thus verifies StandardAdapter's wire format
# end-to-end via the dispatcher.
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


def test_start_web_oauth_returns_auth_url_and_completes_flow(tmp_root):
    """End-to-end: start_web_oauth returns the provider auth URL, hitting the
    spawned callback server triggers exchange_code and save_token, and the
    callback responds with a 302 to success_redirect."""
    cfg = Config(root=tmp_root)
    port = _free_port()

    with patch("personal_db.oauth.exchange_code") as mock_exchange:
        mock_exchange.return_value = {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_at": 9999999999,
        }
        auth_url = start_web_oauth(
            cfg,
            provider="testprov",
            auth_url="https://example.com/auth",
            token_url="https://example.com/token",
            client_id="CID",
            client_secret="CS",
            redirect_host="127.0.0.1",
            redirect_port=port,
            redirect_path="/callback",
            scopes=["a", "b"],
            success_redirect="http://127.0.0.1:8765/setup/foo?msg=ok",
        )

        # The auth URL bakes in the right params.
        parsed = urllib.parse.urlparse(auth_url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "example.com"
        params = dict(urllib.parse.parse_qsl(parsed.query))
        assert params["client_id"] == "CID"
        assert params["response_type"] == "code"
        assert params["redirect_uri"] == f"http://127.0.0.1:{port}/callback"
        assert params["scope"] == "a b"
        state = params["state"]
        assert state  # non-empty

        # Hit the callback as the OAuth provider would.
        cb_url = f"http://127.0.0.1:{port}/callback?state={state}&code=ABC"

        class _NoFollow(urllib.request.HTTPRedirectHandler):
            def http_error_302(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(_NoFollow)
        try:
            opener.open(cb_url, timeout=2)
        except urllib.error.HTTPError as e:
            assert e.code == 302
            assert e.headers["Location"] == "http://127.0.0.1:8765/setup/foo?msg=ok"
        else:
            raise AssertionError("expected 302 redirect")

    # Token was saved via save_token (real, not mocked).
    saved = load_token(cfg, "testprov")
    assert saved is not None
    assert saved["access_token"] == "AT"
    # exchange_code received the right args.
    kwargs = mock_exchange.call_args.kwargs
    assert kwargs["code"] == "ABC"
    assert kwargs["redirect_uri"] == f"http://127.0.0.1:{port}/callback"
    assert kwargs["client_id"] == "CID"


def test_start_web_oauth_state_mismatch_returns_400(tmp_root):
    cfg = Config(root=tmp_root)
    port = _free_port()
    with patch("personal_db.oauth.exchange_code") as mock_exchange:
        start_web_oauth(
            cfg,
            provider="testprov2",
            auth_url="https://example.com/auth",
            token_url="https://example.com/token",
            client_id="CID",
            client_secret="CS",
            redirect_host="127.0.0.1",
            redirect_port=port,
            redirect_path="/callback",
            scopes=[],
            success_redirect="http://127.0.0.1:8765/setup/x",
        )
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/callback?state=WRONG&code=Z", timeout=2
            )
        except urllib.error.HTTPError as e:
            assert e.code == 400
        else:
            raise AssertionError("expected 400")
        mock_exchange.assert_not_called()
        # Token NOT saved.
        assert load_token(cfg, "testprov2") is None
        # Cleanup the still-running session.
        from personal_db.oauth import _shutdown_existing

        _shutdown_existing("testprov2")


def test_exchange_code_dispatches_to_registered_adapter():
    from personal_db.oauth import _adapters, exchange_code, register_adapter

    seen = {}

    class _RecordingAdapter:
        def exchange_code(self, **kw):
            seen.update(kw)
            return {
                "access_token": "from-adapter",
                "refresh_token": "RT",
                "expires_in": 3600,
            }

        def refresh_token(self, **kw):
            return {}

    register_adapter("dispatch_test", _RecordingAdapter())
    try:
        token = exchange_code(
            token_url="https://example.com/token",
            client_id="CID",
            client_secret="CS",
            code="ABC",
            redirect_uri="http://127.0.0.1:1/callback",
            provider="dispatch_test",
        )
        assert token["access_token"] == "from-adapter"
        assert "expires_at" in token
        assert seen["code"] == "ABC"
        assert seen["client_id"] == "CID"
    finally:
        _adapters.pop("dispatch_test", None)


def test_register_and_lookup_adapter():
    from personal_db.oauth import _adapter_for, _adapters, register_adapter, StandardAdapter

    class _Fake:
        def exchange_code(self, **kw): return {}
        def refresh_token(self, **kw): return {}

    fake = _Fake()
    register_adapter("test_provider_xyz", fake)
    try:
        assert _adapter_for("test_provider_xyz") is fake
        # Unknown providers fall back to StandardAdapter
        assert isinstance(_adapter_for("never_registered"), StandardAdapter)
    finally:
        _adapters.pop("test_provider_xyz", None)


def test_refresh_if_needed_dispatches_to_registered_adapter(tmp_root):
    from personal_db.oauth import (
        _adapters,
        load_token,
        refresh_if_needed,
        register_adapter,
        save_token,
    )

    cfg = Config(root=tmp_root)
    # Save an expired token so refresh is forced.
    save_token(cfg, "refresh_dispatch_test", {
        "access_token": "old",
        "refresh_token": "RT",
        "expires_at": 0,
    })

    seen = {}

    class _RecordingAdapter:
        def exchange_code(self, **kw): return {}
        def refresh_token(self, **kw):
            seen.update(kw)
            return {
                "access_token": "from-adapter",
                "refresh_token": "RT2",
                "expires_in": 3600,
            }

    register_adapter("refresh_dispatch_test", _RecordingAdapter())
    try:
        token = refresh_if_needed(
            cfg,
            "refresh_dispatch_test",
            token_url="https://example.com/token",
            client_id="CID",
            client_secret="CS",
        )
        assert token["access_token"] == "from-adapter"
        assert "expires_at" in token
        assert seen["refresh_token"] == "RT"
        # Token was persisted
        saved = load_token(cfg, "refresh_dispatch_test")
        assert saved["access_token"] == "from-adapter"
        assert saved["refresh_token"] == "RT2"
    finally:
        _adapters.pop("refresh_dispatch_test", None)


def test_refresh_if_needed_carries_prior_refresh_token_when_omitted(tmp_root):
    """If the adapter's response omits `refresh_token`, the dispatcher must
    carry the prior one forward (NOT lose it). Withings is the motivating case."""
    from personal_db.oauth import (
        _adapters,
        load_token,
        refresh_if_needed,
        register_adapter,
        save_token,
    )

    cfg = Config(root=tmp_root)
    save_token(cfg, "carryfwd_test", {
        "access_token": "old",
        "refresh_token": "ORIGINAL_RT",
        "expires_at": 0,
    })

    class _OmitRefreshAdapter:
        def exchange_code(self, **kw): return {}
        def refresh_token(self, **kw):
            # Provider returns a new access token but no refresh_token.
            return {"access_token": "NEW_AT", "expires_in": 3600}

    register_adapter("carryfwd_test", _OmitRefreshAdapter())
    try:
        token = refresh_if_needed(
            cfg,
            "carryfwd_test",
            token_url="https://example.com/token",
            client_id="CID",
            client_secret="CS",
        )
        assert token["access_token"] == "NEW_AT"
        # The dispatcher carried the original refresh_token forward.
        assert token["refresh_token"] == "ORIGINAL_RT"
        # And persisted that fact.
        saved = load_token(cfg, "carryfwd_test")
        assert saved["refresh_token"] == "ORIGINAL_RT"
        assert saved["access_token"] == "NEW_AT"
    finally:
        _adapters.pop("carryfwd_test", None)
