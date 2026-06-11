import pytest

from personal_db.worker import install as wi


def test_build_plist_contains_label_keepalive_and_worker_args(tmp_path):
    body = wi.build_plist(
        pdb_path="/usr/local/bin/personal-db",
        root=tmp_path / "personal_db",
        log_path=tmp_path / "personal_db" / "state" / "enrichment-worker.log",
        kind="finance-receipt-v1",
        batch_size=2,
        interval_seconds=123,
        lease_seconds=456,
    )
    assert f"<string>{wi.LABEL}</string>" in body
    assert "<key>KeepAlive</key><true/>" in body
    assert "<key>RunAtLoad</key><true/>" in body
    assert "<string>/usr/local/bin/personal-db</string>" in body
    assert "<string>worker</string>" in body
    assert "<string>enrich</string>" in body
    assert "<string>--kind</string><string>finance-receipt-v1</string>" in body
    assert "<string>--batch-size</string><string>2</string>" in body
    assert "<string>--interval-seconds</string><string>123</string>" in body
    assert "<string>--lease-seconds</string><string>456</string>" in body
    assert str(tmp_path / "personal_db" / "state" / "enrichment-worker.log") in body
    assert "StartInterval" not in body


def test_install_writes_and_loads_plist(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    monkeypatch.setattr(wi, "_LAUNCHAGENTS_DIR", fake_la)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(wi.subprocess, "run", fake_run)

    result = wi.install(
        root=tmp_path / "personal_db",
        kind="finance-receipt-v1",
        batch_size=1,
        interval_seconds=900,
        lease_seconds=1200,
    )

    assert result["plist"] == fake_la / "com.personal_db.enrichment-worker.plist"
    assert result["plist"].exists()
    cmds = [" ".join(cmd) for cmd, _kw in calls]
    assert any("unload" in cmd and "com.personal_db.enrichment-worker" in cmd for cmd in cmds)
    assert any("load" in cmd and "com.personal_db.enrichment-worker" in cmd for cmd in cmds)


def test_uninstall_removes_plist(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    plist = fake_la / "com.personal_db.enrichment-worker.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(wi, "_LAUNCHAGENTS_DIR", fake_la)
    monkeypatch.setattr(
        wi.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )

    wi.uninstall()

    assert not plist.exists()


def test_info_reports_loaded_worker(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    plist = fake_la / "com.personal_db.enrichment-worker.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(wi, "_LAUNCHAGENTS_DIR", fake_la)

    status_text = """
{
    "Label" = "com.personal_db.enrichment-worker";
    "LastExitStatus" = 0;
    "Program" = "/usr/local/bin/personal-db";
};
"""

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = status_text
            stderr = ""

        return R()

    monkeypatch.setattr(wi.subprocess, "run", fake_run)

    result = wi.info(tmp_path / "personal_db")

    assert result["installed"] is True
    assert result["loaded"] is True
    assert result["last_exit_status"] == 0
    assert result["program"] == "/usr/local/bin/personal-db"


def test_log_tail_returns_last_lines(tmp_path):
    root = tmp_path / "personal_db"
    log_path = root / "state" / "enrichment-worker.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("one\ntwo\nthree\n")

    result = wi.log_tail(root, lines=2)

    assert result["exists"] is True
    assert result["path"] == str(log_path)
    assert result["lines"] == ["two", "three"]


def test_log_tail_handles_missing_log(tmp_path):
    root = tmp_path / "personal_db"

    result = wi.log_tail(root, lines=2)

    assert result["exists"] is False
    assert result["lines"] == []


def test_resolve_personal_db_executable_prefers_current_python_env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    script = bin_dir / "personal-db"
    python.write_text("")
    script.write_text("")
    monkeypatch.setattr(wi.sys, "executable", str(python))
    monkeypatch.setattr(wi.shutil, "which", lambda _: "/stale/personal-db")

    assert wi._resolve_personal_db_executable() == str(script)


def test_install_raises_if_personal_db_not_on_path(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    monkeypatch.setattr(wi, "_LAUNCHAGENTS_DIR", fake_la)
    monkeypatch.setattr(wi.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(wi.shutil, "which", lambda _: None)

    with pytest.raises(RuntimeError, match="not found on PATH"):
        wi.install(root=tmp_path / "personal_db")


def test_install_raises_runtime_error_on_launchctl_load_failure(tmp_path, monkeypatch):
    import subprocess as sp

    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    monkeypatch.setattr(wi, "_LAUNCHAGENTS_DIR", fake_la)

    def fake_run(cmd, **kw):
        if "load" in cmd and kw.get("check"):
            raise sp.CalledProcessError(1, cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(wi.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="launchctl failed to load"):
        wi.install(root=tmp_path / "personal_db")
