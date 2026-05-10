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


def _load_ingest_module():
    spec = importlib.util.spec_from_file_location(
        "withings_ingest_test", WITHINGS_DIR / "ingest.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_flatten_full_measuregrp():
    ingest = _load_ingest_module()
    grp = {
        "grpid": 12345,
        "attrib": 0,
        "date": 1746752400,        # 2025-05-09T00:20:00Z (well-defined epoch)
        "created": 1746752400,
        "modified": 1746752400,
        "category": 1,
        "deviceid": "abc",
        "timezone": "America/Los_Angeles",
        "measures": [
            {"value": 80123, "type": 1, "unit": -3},   # weight 80.123 kg
            {"value": 18234, "type": 6, "unit": -3},   # fat ratio 18.234 %
            {"value": 14567, "type": 8, "unit": -3},   # fat mass 14.567 kg
            {"value": 65556, "type": 5, "unit": -3},   # lean mass 65.556 kg
            {"value": 60123, "type": 76, "unit": -3},  # muscle 60.123 kg
            {"value": 3210,  "type": 88, "unit": -3},  # bone 3.210 kg
            {"value": 45678, "type": 77, "unit": -3},  # hydration 45.678 kg
            {"value": 72,    "type": 11, "unit": 0},   # heart pulse 72 bpm
        ],
    }
    row = ingest._flatten(grp, default_tz="UTC")
    assert row["grpid"] == "12345"
    assert row["timezone"] == "America/Los_Angeles"
    assert row["attrib"] == 0
    assert row["category"] == 1
    assert row["device_id"] == "abc"
    assert row["weight_kg"] == 80.123
    assert row["fat_ratio_pct"] == 18.234
    assert row["fat_mass_kg"] == 14.567
    assert row["lean_mass_kg"] == 65.556
    assert row["muscle_mass_kg"] == 60.123
    assert row["bone_mass_kg"] == 3.210
    assert row["hydration_kg"] == 45.678
    assert row["heart_pulse_bpm"] == 72
    assert row["date"].endswith("+00:00")
    assert row["_modified_unix"] == 1746752400


def test_flatten_partial_only_weight():
    ingest = _load_ingest_module()
    grp = {
        "grpid": 99,
        "attrib": 0,
        "date": 1746752400,
        "created": 1746752400,
        "modified": 1746752400,
        "category": 1,
        "deviceid": "abc",
        "timezone": "UTC",
        "measures": [{"value": 75000, "type": 1, "unit": -3}],
    }
    row = ingest._flatten(grp, default_tz="UTC")
    assert row["weight_kg"] == 75.0
    assert row["fat_ratio_pct"] is None
    assert row["fat_mass_kg"] is None
    assert row["lean_mass_kg"] is None
    assert row["heart_pulse_bpm"] is None


def test_flatten_unknown_measure_type_dropped():
    ingest = _load_ingest_module()
    grp = {
        "grpid": 1, "attrib": 0, "date": 1746752400, "created": 1746752400,
        "modified": 1746752400, "category": 1, "deviceid": "x", "timezone": "UTC",
        "measures": [
            {"value": 80000, "type": 1, "unit": -3},
            {"value": 999,   "type": 4242, "unit": 0},  # unknown type
        ],
    }
    row = ingest._flatten(grp, default_tz="UTC")
    assert row["weight_kg"] == 80.0
    assert "4242" not in row  # not stored as a column


def test_flatten_timezone_fallback_to_default():
    ingest = _load_ingest_module()
    grp = {
        "grpid": 1, "attrib": 0, "date": 1746752400, "created": 1746752400,
        "modified": 1746752400, "category": 1, "deviceid": "x",
        # no per-row timezone field
        "measures": [{"value": 80000, "type": 1, "unit": -3}],
    }
    row = ingest._flatten(grp, default_tz="America/New_York")
    assert row["timezone"] == "America/New_York"
