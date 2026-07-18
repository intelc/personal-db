import pytest

from personal_db.services.daemon import install as di


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


def test_install_writes_and_loads_plist(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    new_plist = fake_la / "com.personal_db.daemon.plist"

    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)
    # _resolve_cli_binary() falls back to shutil.which("personal-db") when
    # argv[0] isn't a personal-db entry point (true for the pytest runner
    # process). CI runners don't have the venv's bin/ on PATH, so without this
    # the test fails before touching any of the plist-writing behavior it's
    # meant to exercise. Pin the resolved binary path instead of depending on
    # the test runner's PATH.
    monkeypatch.setattr(di.shutil, "which", lambda _name: "/usr/local/bin/personal-db")

    calls: list[list[str]] = []
    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr(di.subprocess, "run", fake_run)

    result = di.install(root=tmp_path / "personal_db")

    assert result["plist"] == new_plist
    assert new_plist.exists(), "daemon plist should be written"
    cmds = [" ".join(c) for c in calls]
    assert any("load" in c and "com.personal_db.daemon" in c for c in cmds)


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


def test_install_raises_runtime_error_on_launchctl_load_failure(tmp_path, monkeypatch):
    """When launchctl load exits non-zero, install() should raise RuntimeError
    with a clear message pointing the user to re-run after fixing the issue."""
    import subprocess as _sp

    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)
    # See test_install_writes_and_loads_plist: pin the resolved binary so this
    # test exercises launchctl-failure handling, not the runner's PATH.
    monkeypatch.setattr(di.shutil, "which", lambda _name: "/usr/local/bin/personal-db")

    def fake_run(cmd, **kw):
        # Raise CalledProcessError only on the "load" call.
        if "load" in cmd and kw.get("check"):
            raise _sp.CalledProcessError(1, cmd)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(di.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="launchctl failed to load"):
        di.install(root=tmp_path / "personal_db")
