from personal_db.wizard.env_file import read_env, upsert_env


def test_read_env_missing_file_returns_empty(tmp_path):
    assert read_env(tmp_path / "nope.env") == {}


def test_upsert_env_creates_file_mode_0600(tmp_path):
    p = tmp_path / ".env"
    upsert_env(p, "KEY", "value")
    assert read_env(p) == {"KEY": "value"}
    assert oct(p.stat().st_mode)[-3:] == "600"


def test_upsert_env_updates_existing_key(tmp_path):
    p = tmp_path / ".env"
    upsert_env(p, "KEY", "v1")
    upsert_env(p, "KEY", "v2")
    assert read_env(p) == {"KEY": "v2"}


def test_upsert_env_appends_new_key_preserving_existing(tmp_path):
    p = tmp_path / ".env"
    upsert_env(p, "A", "1")
    upsert_env(p, "B", "2")
    assert read_env(p) == {"A": "1", "B": "2"}


def test_upsert_env_preserves_comments_and_blank_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# header\n\nA=1\n# section\nB=2\n")
    upsert_env(p, "B", "two")
    text = p.read_text()
    assert "# header" in text
    assert "# section" in text
    assert "B=two" in text
    assert "A=1" in text


def test_read_env_strips_quotes(tmp_path):
    p = tmp_path / ".env"
    p.write_text('A="hello world"\nB=plain\n')
    assert read_env(p) == {"A": "hello world", "B": "plain"}


def test_upsert_env_quotes_values_with_spaces(tmp_path):
    p = tmp_path / ".env"
    upsert_env(p, "K", "hello world")
    assert 'K="hello world"' in p.read_text()
