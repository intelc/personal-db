from pathlib import Path

import pytest

from personal_db.daemon import install as di


def test_build_plist_contains_label_keepalive_and_args(tmp_path):
    body = di.build_plist(
        pdb_path="/usr/local/bin/personal-db",
        root=tmp_path / "personal_db",
        log_path=tmp_path / "personal_db" / "state" / "daemon.log",
    )
    assert f"<string>{di.LABEL}</string>" in body
    assert "<key>KeepAlive</key><true/>" in body
    assert "<key>RunAtLoad</key><true/>" in body
    assert "<string>/usr/local/bin/personal-db</string>" in body
    assert "<string>daemon</string>" in body
    assert "<string>run</string>" in body
    assert str(tmp_path / "personal_db" / "state" / "daemon.log") in body
    # Should NOT include StartInterval — daemon is long-running, not periodic.
    assert "StartInterval" not in body


def test_install_migrates_old_scheduler_plist(tmp_path, monkeypatch):
    """When the old com.personal_db.scheduler.plist exists, install() should
    unload+delete it before writing the new daemon plist."""
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    old_plist = fake_la / "com.personal_db.scheduler.plist"
    old_plist.write_text("<plist/>")  # contents irrelevant, presence is what matters
    new_plist = fake_la / "com.personal_db.daemon.plist"

    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)

    calls: list[list[str]] = []
    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr(di.subprocess, "run", fake_run)

    di.install(root=tmp_path / "personal_db")

    assert not old_plist.exists(), "old scheduler plist should be deleted"
    assert new_plist.exists(), "new daemon plist should be written"
    cmds = [" ".join(c) for c in calls]
    assert any("unload" in c and "com.personal_db.scheduler" in c for c in cmds)
    assert any("load" in c and "com.personal_db.daemon" in c for c in cmds)


def test_install_when_no_old_plist(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)
    monkeypatch.setattr(di.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    p = di.install(root=tmp_path / "personal_db")
    assert p == fake_la / "com.personal_db.daemon.plist"
    assert p.exists()


def test_uninstall_removes_plist(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    new_plist = fake_la / "com.personal_db.daemon.plist"
    new_plist.write_text("<plist/>")
    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)
    monkeypatch.setattr(di.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    di.uninstall()
    assert not new_plist.exists()


def test_build_plist_escapes_xml_special_chars(tmp_path):
    """Paths containing &, <, > must be XML-escaped to keep the plist valid."""
    body = di.build_plist(
        pdb_path="/usr/local/bin/personal-db",
        root=tmp_path / "data & stuff",
        log_path=tmp_path / "data & stuff" / "daemon.log",
    )
    assert "data &amp; stuff" in body
    assert "data & stuff" not in body
    # Must still parse as well-formed XML.
    import xml.etree.ElementTree as ET
    ET.fromstring(body)


def test_install_raises_if_personal_db_not_on_path(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)
    monkeypatch.setattr(di.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        di.install(root=tmp_path / "personal_db")
