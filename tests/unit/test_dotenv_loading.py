import os


def test_dotenv_load_function_reads_root_env(tmp_path, monkeypatch):
    """Direct unit test of the load helper."""
    from personal_db.cli.main import _load_root_env

    root = tmp_path / "personal_db"
    root.mkdir()
    (root / ".env").write_text("PERSONAL_DB_TEST_X=loaded\n")
    monkeypatch.delenv("PERSONAL_DB_TEST_X", raising=False)
    _load_root_env(root)
    assert os.environ.get("PERSONAL_DB_TEST_X") == "loaded"


def test_dotenv_does_not_override_real_env(tmp_path, monkeypatch):
    """override=False — shell env wins over .env (test/debug behavior)."""
    from personal_db.cli.main import _load_root_env

    root = tmp_path / "personal_db"
    root.mkdir()
    (root / ".env").write_text("PERSONAL_DB_TEST_Y=from-env-file\n")
    monkeypatch.setenv("PERSONAL_DB_TEST_Y", "from-shell")
    _load_root_env(root)
    assert os.environ.get("PERSONAL_DB_TEST_Y") == "from-shell"


def test_dotenv_load_silent_when_env_missing(tmp_path):
    from personal_db.cli.main import _load_root_env

    # Should not raise even though no .env exists
    _load_root_env(tmp_path / "personal_db")
