from pathlib import Path

from personal_db.scheduler import LABEL, build_plist


def test_build_plist_contains_label_and_interval():
    body = build_plist(
        pdb_path="/usr/local/bin/personal-db",
        root=Path("/Users/me/personal_db"),
        interval_seconds=600,
        log_path=Path("/Users/me/personal_db/state/scheduler.log"),
    )
    assert f"<string>{LABEL}</string>" in body
    assert "<integer>600</integer>" in body
    assert "/usr/local/bin/personal-db" in body
    assert "/Users/me/personal_db/state/scheduler.log" in body
    assert "<string>sync</string>" in body
    assert "<string>--due</string>" in body
