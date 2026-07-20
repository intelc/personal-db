"""Keychain-backed updater-signing script contracts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = REPO_ROOT / "packaging" / "setup-updater-keychain.sh"
RELEASE_SCRIPT = REPO_ROOT / "packaging" / "release.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _fake_security(tmp_path: Path) -> tuple[Path, Path]:
    log_path = tmp_path / "security-calls.jsonl"
    binary = tmp_path / "security"
    _write_executable(
        binary,
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_SECURITY_LOG"], "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "find-generic-password":
    if os.environ.get("FAKE_SECURITY_MISSING") == "1":
        raise SystemExit(44)
    if "-w" in sys.argv[2:]:
        print(os.environ.get("FAKE_SECURITY_PASSWORD", "test-password"))
""",
    )
    return binary, log_path


def _fake_git(path: Path) -> None:
    _write_executable(path / "git", "#!/usr/bin/env bash\nexit 0\n")


def _run_release(tmp_path: Path, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_git(fake_bin)
    environment = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TAURI_SIGNING_PRIVATE_KEY": str(tmp_path / "updater.key"),
    } | extra_env
    (tmp_path / "updater.key").write_text("not examined before preflight")
    return subprocess.run(
        [str(RELEASE_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_setup_prompts_without_secret_in_argv_and_limits_security_acl(tmp_path: Path) -> None:
    security, log_path = _fake_security(tmp_path)
    result = subprocess.run(
        [str(SETUP_SCRIPT)],
        env=os.environ | {
            "PERSONAL_DB_UPDATER_KEYCHAIN_SECURITY_BIN": str(security),
            "PERSONAL_DB_UPDATER_KEYCHAIN_SERVICE": "test.service",
            "PERSONAL_DB_UPDATER_KEYCHAIN_ACCOUNT": "test.account",
            "FAKE_SECURITY_LOG": str(log_path),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    add, find, password_read = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert add == [
        "add-generic-password",
        "-U",
        "-s",
        "test.service",
        "-a",
        "test.account",
        "-T",
        "/usr/bin/security",
        "-w",
    ]
    assert find == ["find-generic-password", "-s", "test.service", "-a", "test.account"]
    assert password_read == ["find-generic-password", "-s", "test.service", "-a", "test.account", "-w"]
    assert "test-password" not in result.stdout + result.stderr


def test_setup_help_and_invalid_arguments_never_call_security(tmp_path: Path) -> None:
    security, log_path = _fake_security(tmp_path)
    environment = os.environ | {
        "PERSONAL_DB_UPDATER_KEYCHAIN_SECURITY_BIN": str(security),
        "FAKE_SECURITY_LOG": str(log_path),
    }

    help_result = subprocess.run([str(SETUP_SCRIPT), "--help"], env=environment, capture_output=True, text=True)
    invalid_result = subprocess.run([str(SETUP_SCRIPT), "unexpected"], env=environment, capture_output=True, text=True)

    assert help_result.returncode == 0
    assert "Usage:" in help_result.stdout
    assert invalid_result.returncode != 0
    assert "unknown argument" in invalid_result.stderr
    assert not log_path.exists()


def test_setup_removes_empty_password_item(tmp_path: Path) -> None:
    security, log_path = _fake_security(tmp_path)
    result = subprocess.run(
        [str(SETUP_SCRIPT)],
        env=os.environ | {
            "PERSONAL_DB_UPDATER_KEYCHAIN_SECURITY_BIN": str(security),
            "FAKE_SECURITY_LOG": str(log_path),
            "FAKE_SECURITY_PASSWORD": "",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "entered updater password was empty" in result.stderr
    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert calls[-1] == [
        "delete-generic-password",
        "-s",
        "com.personaldb.updater-signing",
        "-a",
        "updater-key-password",
    ]


def test_keychain_mode_missing_item_fails_before_build_and_waives_tty(tmp_path: Path) -> None:
    security, log_path = _fake_security(tmp_path)
    result = _run_release(
        tmp_path,
        "--password-from-keychain",
        "--notes",
        "release notes",
        PERSONAL_DB_UPDATER_KEYCHAIN_SECURITY_BIN=str(security),
        FAKE_SECURITY_LOG=str(log_path),
        FAKE_SECURITY_MISSING="1",
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "updater password is missing from the Keychain" in combined
    assert "stdin is not a TTY" not in combined
    assert "step 1: sync versions" not in combined
    assert [json.loads(line) for line in log_path.read_text().splitlines()] == [
        [
            "find-generic-password",
            "-s",
            "com.personaldb.updater-signing",
            "-a",
            "updater-key-password",
            "-w",
        ]
    ]


def test_keychain_password_is_not_logged_or_exported_to_preflight_children(tmp_path: Path) -> None:
    security, log_path = _fake_security(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_git(fake_bin)
    seen_password = tmp_path / "gh-password"
    _write_executable(
        fake_bin / "gh",
        "#!/usr/bin/env bash\nprintf '%s' \"${TAURI_SIGNING_PRIVATE_KEY_PASSWORD-}\" > \"$FAKE_GH_PASSWORD\"\nexit 1\n",
    )
    key = tmp_path / "updater.key"
    key.write_text("not examined before gh preflight")
    secret = "correct-horse-battery-staple"
    result = subprocess.run(
        [str(RELEASE_SCRIPT), "--password-from-keychain", "--notes", "release notes"],
        cwd=REPO_ROOT,
        env=os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TAURI_SIGNING_PRIVATE_KEY": str(key),
            "PERSONAL_DB_UPDATER_KEYCHAIN_SECURITY_BIN": str(security),
            "FAKE_SECURITY_LOG": str(log_path),
            "FAKE_SECURITY_PASSWORD": secret,
            "FAKE_GH_PASSWORD": str(seen_password),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "gh is not authenticated" in result.stderr
    assert secret not in result.stdout + result.stderr
    assert seen_password.read_text() == ""
    assert len(log_path.read_text().splitlines()) == 1


def test_interactive_path_still_requires_a_tty_without_keychain_flag(tmp_path: Path) -> None:
    result = _run_release(tmp_path, "--notes", "release notes")

    assert result.returncode != 0
    assert "stdin is not a TTY" in result.stderr


def test_keychain_password_is_scoped_to_signer_children_and_rejected_password_has_guidance() -> None:
    release = RELEASE_SCRIPT.read_text()

    assert "export TAURI_SIGNING_PRIVATE_KEY_PASSWORD" not in release
    assert 'TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$password" "$@"' in release
    assert "unset password" in release
    assert "stored updater-key password was rejected; rerun $SCRIPT_DIR/setup-updater-keychain.sh" in release
    assert 'run_with_keychain_password "step 3 tauri build" build_with_updater_artifacts' in release
    assert 'run_with_keychain_password "step 4b signer sign" sign_updater_archive' in release
