import sys

import pytest

from personal_db.core.config import Config
from personal_db.core.installer import install_template, is_outdated, list_bundled, update_template
from personal_db.core.manifest import PlatformUnsupportedError
from personal_db.core.validation import is_validated


def test_list_bundled_returns_known_templates():
    names = set(list_bundled())
    # These connectors ship with the package; new ones extend this set.
    assert {
        "github_commits",
        "granola",
        "whoop",
        "screen_time",
        "imessage",
        "habits",
        "code_agent_activity",
    } <= names
    # claude_conversations and codex_conversations were folded into code_agent_activity.
    assert "claude_conversations" not in names
    assert "codex_conversations" not in names


def test_granola_manifest_loads():
    from pathlib import Path

    from personal_db.core.manifest import load_manifest

    here = Path(__file__).resolve().parents[2]
    m = load_manifest(here / "src/personal_db/templates/trackers/granola/manifest.yaml")
    assert m.name == "granola"
    assert m.permission_type == "api_key"
    assert "granola_documents" in m.schema.tables


def test_whoop_manifest_loads_with_instructions_step():
    """The web setup wizard now shows the redirect URI to register before
    the Authorize button, mirroring oura's instructions step."""
    from pathlib import Path

    from personal_db.core.manifest import InstructionsStep, OAuthStep, load_manifest

    here = Path(__file__).resolve().parents[2]
    m = load_manifest(here / "src/personal_db/templates/trackers/whoop/manifest.yaml")
    assert m.name == "whoop"
    assert m.permission_type == "oauth"
    assert isinstance(m.setup_steps[0], InstructionsStep)
    assert "http://localhost:9876/callback" in m.setup_steps[0].text
    assert "developer.whoop.com" in m.setup_steps[0].text
    oauth_step = next(s for s in m.setup_steps if isinstance(s, OAuthStep))
    assert oauth_step.redirect_port == 9876


def test_withings_manifest_loads_with_instructions_step():
    from pathlib import Path

    from personal_db.core.manifest import InstructionsStep, OAuthStep, load_manifest

    here = Path(__file__).resolve().parents[2]
    m = load_manifest(here / "src/personal_db/templates/trackers/withings/manifest.yaml")
    assert m.name == "withings"
    assert m.permission_type == "oauth"
    assert isinstance(m.setup_steps[0], InstructionsStep)
    assert "http://localhost:9877/callback" in m.setup_steps[0].text
    assert "developer.withings.com" in m.setup_steps[0].text
    oauth_step = next(s for s in m.setup_steps if isinstance(s, OAuthStep))
    assert oauth_step.redirect_port == 9877


def test_install_template_copies_tree(tmp_root):
    cfg = Config(root=tmp_root)
    dest = install_template(cfg, "habits")
    assert dest == tmp_root / "trackers" / "habits"
    assert (dest / "manifest.yaml").exists()
    assert (dest / "schema.sql").exists()
    assert (dest / "ingest.py").exists()


def test_install_template_raises_on_already_installed(tmp_root):
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    with pytest.raises(FileExistsError):
        install_template(cfg, "habits")


def test_install_template_auto_stamps_validation(tmp_root):
    """Bundled templates are pre-trusted: install_template should stamp them
    as validated so sync_one's gate (core/validation.py) doesn't make a user
    run `tracker validate` just to use a tracker we shipped."""
    cfg = Config(root=tmp_root)
    dest = install_template(cfg, "habits")
    assert is_validated(cfg, "habits", dest) is True


def test_update_template_auto_stamps_validation(tmp_root):
    cfg = Config(root=tmp_root)
    dest = install_template(cfg, "habits")
    # Simulate hand-edited drift, then reinstall from the bundle.
    (dest / "ingest.py").write_text("# tampered\n" + (dest / "ingest.py").read_text())
    assert is_validated(cfg, "habits", dest) is False
    update_template(cfg, "habits")
    assert is_validated(cfg, "habits", dest) is True


def test_install_template_raises_on_unknown(tmp_root):
    cfg = Config(root=tmp_root)
    with pytest.raises(ValueError):
        install_template(cfg, "no_such_tracker_xyz")


def test_is_outdated_false_when_files_match(tmp_root):
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    assert is_outdated(cfg, "habits") is False


def test_is_outdated_true_when_files_differ(tmp_root):
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    # Mutate one file to simulate drift
    (tmp_root / "trackers" / "habits" / "manifest.yaml").write_text("name: hacked\n")
    assert is_outdated(cfg, "habits") is True


def test_is_outdated_false_when_not_installed(tmp_root):
    cfg = Config(root=tmp_root)
    assert is_outdated(cfg, "habits") is False


def test_is_outdated_false_for_custom_tracker(tmp_root):
    """A user-created tracker (no bundled template) is never marked outdated."""
    cfg = Config(root=tmp_root)
    custom = tmp_root / "trackers" / "my_custom_thing"
    custom.mkdir(parents=True)
    (custom / "manifest.yaml").write_text("name: custom\n")
    assert is_outdated(cfg, "my_custom_thing") is False


def test_update_template_overwrites_files(tmp_root):
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    # Mutate the installed manifest
    p = tmp_root / "trackers" / "habits" / "manifest.yaml"
    p.write_text("name: hacked\n")
    update_template(cfg, "habits")
    # Should be restored from bundle
    assert "hacked" not in p.read_text()
    assert p.read_text().startswith("name: habits")


def test_update_template_preserves_other_files(tmp_root):
    """If the user has a side file (e.g., notes) in the tracker dir, update doesn't touch it."""
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    side = tmp_root / "trackers" / "habits" / "user_notes.md"
    side.write_text("personal notes")
    update_template(cfg, "habits")
    assert side.exists()
    assert side.read_text() == "personal notes"


def test_update_template_copies_oauth_adapter_modules(tmp_root):
    """When the manifest declares OAuthStep.adapter, update_template must
    also copy the adapter module file. This was a real bug — Withings was the
    first bundled tracker to declare an adapter."""
    cfg = Config(root=tmp_root)
    dest = update_template(cfg, "withings")
    assert (dest / "oauth_adapter.py").is_file(), (
        f"oauth_adapter.py was not copied to {dest}; only "
        f"{sorted(p.name for p in dest.iterdir())} present"
    )
    # Sanity: importing it works and the class is loadable.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "withings_adapter_install_test", dest / "oauth_adapter.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "WithingsAdapter")


def test_is_outdated_detects_adapter_module_drift(tmp_root, monkeypatch):
    """If the installed adapter module diverges from the bundle, is_outdated
    should return True. Without hashing the adapter, drift goes undetected."""
    cfg = Config(root=tmp_root)
    dest = update_template(cfg, "withings")
    assert not is_outdated(cfg, "withings"), "freshly-installed should be in sync"

    # Mutate the installed adapter — should be detected as drift.
    adapter_path = dest / "oauth_adapter.py"
    adapter_path.write_text(adapter_path.read_text() + "\n# manual edit\n")
    assert is_outdated(cfg, "withings"), (
        "adapter module drift was not detected by is_outdated"
    )


def test_install_template_refuses_unsupported_platform(tmp_root, monkeypatch):
    """imessage declares `platform: [darwin]`; installing it on a simulated
    non-macOS OS must refuse with a clear message rather than copy files."""
    cfg = Config(root=tmp_root)
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(PlatformUnsupportedError, match="imessage requires macOS"):
        install_template(cfg, "imessage")
    assert not (tmp_root / "trackers" / "imessage").exists()


def test_install_template_allows_supported_platform(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    monkeypatch.setattr(sys, "platform", "darwin")
    dest = install_template(cfg, "imessage")
    assert dest.exists()


def test_update_template_refuses_unsupported_platform(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    # The initial install must succeed regardless of the OS this test runs on
    # (it fails outright on a real Linux CI runner otherwise, since
    # sys.platform is already "linux" before any monkeypatching happens);
    # pin it to a supported platform first, then switch to "linux" for the
    # actual assertion under test.
    monkeypatch.setattr(sys, "platform", "darwin")
    install_template(cfg, "imessage")
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(PlatformUnsupportedError, match="imessage requires macOS"):
        update_template(cfg, "imessage")


def test_monarch_manifest_version_and_migration_file_shipped():
    """monarch is schema_version 2; its migrations/ dir must ship the
    account-exports rebuild file the schema-version bump depends on."""
    from pathlib import Path

    here = Path(__file__).resolve().parents[2]
    monarch_dir = here / "src/personal_db/templates/trackers/monarch"
    from personal_db.core.manifest import load_manifest

    m = load_manifest(monarch_dir / "manifest.yaml")
    assert m.schema_version == 2
    migration_files = sorted(p.name for p in (monarch_dir / "migrations").glob("*.sql"))
    assert migration_files == ["002_account_exports_rebuild.sql"]


def test_update_template_copies_migrations_dir(tmp_root):
    """reinstall (update_template) must mirror migrations/*.sql into the
    installed tracker dir, not just the four canonical files -- otherwise a
    tracker installed before a migration was added never gets it."""
    cfg = Config(root=tmp_root)
    dest = update_template(cfg, "monarch")
    assert (dest / "migrations" / "002_account_exports_rebuild.sql").is_file()
