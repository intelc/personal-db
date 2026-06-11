import os
import subprocess
import sys


def test_source_install_and_list(tmp_path):
    root = tmp_path / "personal_db"

    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "source",
            "install",
            "spark_email",
        ],
        capture_output=True,
        text=True,
    )

    assert install.returncode == 0, install.stderr
    assert (root / "sources" / "spark_email" / "source.yaml").exists()

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "source",
            "list",
        ],
        capture_output=True,
        text=True,
    )

    assert listed.returncode == 0, listed.stderr
    assert "spark_email" in listed.stdout
    assert "enabled" in listed.stdout


def test_source_spark_folders_uses_spark_cli(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    spark = bin_dir / "spark"
    spark.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"folders\" ]; then\n"
        "  cat <<'EOF'\n"
        "Unified\n"
        "  Inbox       3 messages  (Inbox)\n"
        "  --------------------------------------\n"
        "  Total       3 messages\n"
        "EOF\n"
        "elif [ \"$1\" = \"--version\" ]; then\n"
        "  echo 'spark test'\n"
        "else\n"
        "  echo \"unexpected args: $@\" >&2\n"
        "  exit 1\n"
        "fi\n"
    )
    spark.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "source",
            "spark",
            "folders",
        ],
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"source": "spark_email"' in result.stdout
    assert '"total": 3' in result.stdout
    assert '"identifier": "Inbox"' in result.stdout


def test_context_email_search_receipts_uses_installed_spark_source(tmp_path):
    root = tmp_path / "personal_db"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "source",
            "install",
            "spark_email",
        ],
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    spark = bin_dir / "spark"
    spark.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"search\" ]; then\n"
        "  echo \"$@\" >> \"$SPARK_ARGS_FILE\"\n"
        "  cat <<'EOF'\n"
        "Emails matching receipt\n"
        "  123  user@example.com  Store <store@example.com>  2026-06-01  Receipt\n"
        "Page 1 of 1 (1 total emails)\n"
        "EOF\n"
        "elif [ \"$1\" = \"--version\" ]; then\n"
        "  echo 'spark test'\n"
        "else\n"
        "  echo \"unexpected args: $@\" >&2\n"
        "  exit 1\n"
        "fi\n"
    )
    spark.chmod(0o755)
    args_file = tmp_path / "spark_args.txt"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "SPARK_ARGS_FILE": str(args_file),
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "context",
            "email",
            "search-receipts",
            "--merchant",
            "Store",
            "--amount",
            "12.34",
            "--date",
            "2026-06-01",
            "--window-days",
            "1",
        ],
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"provider": "email"' in result.stdout
    assert '"ref": "spark_email:message:123"' in result.stdout
    args_text = args_file.read_text()
    assert "Store receipt invoice order confirmation 12.34" in args_text
    assert "after:2026/05/31 before:2026/06/03" in args_text
    assert "\nsearch --filter after:2026/05/31 before:2026/06/03 12.34\n" in f"\n{args_text}"
