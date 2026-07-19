import typer

from personal_db.cli.state import get_root
from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, init_db
from personal_db.core.installer import install_template, update_template
from personal_db.core.manifest import PlatformUnsupportedError, load_manifest
from personal_db.core.migrations import apply_pending_migrations
from personal_db.core.pack_deps import DepsInstallError, install_tracker_deps, tracker_python_deps
from personal_db.core.scaffold import scaffold_tracker
from personal_db.core.validation import validate_tracker
from personal_db.services.wizard.menu import run_menu
from personal_db.services.wizard.runner import run_tracker


def new(name: str) -> None:
    """Scaffold a new tracker."""
    cfg = Config(root=get_root())
    try:
        dest = scaffold_tracker(cfg, name)
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(f"Created tracker at {dest}")


def list_cmd() -> None:
    """List installed trackers and their last-sync state."""
    root = get_root()
    trackers_dir = root / "trackers"
    if not trackers_dir.exists() or not any(trackers_dir.iterdir()):
        typer.echo(
            "No trackers installed. Use `personal-db tracker new <name>` or"
            " `personal-db tracker install <builtin>`."
        )
        return
    for d in sorted(trackers_dir.iterdir()):
        if d.is_dir() and (d / "manifest.yaml").exists():
            m = load_manifest(d / "manifest.yaml")
            typer.echo(f"  {m.name:20s} {m.permission_type:18s} {m.description}")


def install(name: str) -> None:
    """Copy a bundled tracker template into the user's trackers/ directory."""
    cfg = Config(root=get_root())
    try:
        dest = install_template(cfg, name)
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    except PlatformUnsupportedError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    # Apply schema eagerly so manual-capture trackers (life_context, habits)
    # have their tables ready without needing a no-op sync first.
    manifest = load_manifest(dest / "manifest.yaml")
    init_db(cfg.db_path)
    apply_pending_migrations(cfg, name, dest, manifest)
    apply_tracker_schema(cfg.db_path, (dest / "schema.sql").read_text())
    typer.echo(f"Installed {name} -> {dest}")


def reinstall(name: str) -> None:
    """Overwrite an installed tracker's files from the bundled template.

    Use this after editing a bundled template (manifest.yaml / ingest.py /
    schema.sql / visualizations.py) — `personal-db sync` runs the *installed*
    copy at <root>/trackers/<name>/, so template edits don't take effect until
    the installed copy is refreshed. Re-applies schema.sql so additive column
    changes land on the live DB (running any pending migrations first)."""
    cfg = Config(root=get_root())
    try:
        dest = update_template(cfg, name)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    except PlatformUnsupportedError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    manifest = load_manifest(dest / "manifest.yaml")
    init_db(cfg.db_path)
    apply_pending_migrations(cfg, name, dest, manifest)
    apply_tracker_schema(cfg.db_path, (dest / "schema.sql").read_text())
    typer.echo(f"Reinstalled {name} -> {dest}")


def validate(name: str) -> None:
    """Lint-check a tracker's files and, if they pass, stamp them as validated.

    `personal-db sync`/`backfill` refuse to run a tracker whose current files
    don't match a validation stamp (core/validation.py) — this is how you
    clear that gate after hand-editing a tracker's ingest.py/manifest.yaml/
    schema.sql. Bundled templates are stamped automatically by
    `tracker install`/`tracker reinstall`; this command is for
    custom/agent-authored trackers."""
    cfg = Config(root=get_root())
    try:
        result = validate_tracker(cfg, name)
    except (ValueError, FileNotFoundError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    for check in result["checks"]:
        mark = "✓" if check["ok"] else "✗"
        typer.echo(f"  {mark} {check['name']}: {check['detail']}")
    if result["ok"]:
        typer.echo(f"{name}: validated — sync will accept these files")
    else:
        typer.echo(f"{name}: validation failed — sync will refuse these files", err=True)
        raise typer.Exit(1)


def deps(
    name: str = typer.Argument(None),
    all_: bool = typer.Option(
        False, "--all", help="Install declared python_deps for every installed tracker"
    ),
) -> None:
    """Install a tracker's declared python_deps into <root>/lib/.

    The sealed, signed app bundle can't have packages added to its own
    site-packages -- this is how a custom tracker gets a third-party
    dependency the bundle doesn't ship (see core/runtime_env.py). Safe to
    re-run: uses `pip install --target --upgrade`, so it also picks up a
    changed pin after editing python_deps in manifest.yaml.
    """
    if name is not None and all_:
        typer.echo("specify either a tracker name or --all, not both", err=True)
        raise typer.Exit(2)
    if name is None and not all_:
        typer.echo("specify a tracker name or --all", err=True)
        raise typer.Exit(2)

    cfg = Config(root=get_root())
    if all_:
        trackers_dir = cfg.trackers_dir
        if not trackers_dir.exists():
            typer.echo("no trackers installed")
            return
        names = sorted(
            d.name
            for d in trackers_dir.iterdir()
            if d.is_dir() and (d / "manifest.yaml").is_file()
        )
    else:
        names = [name]

    exit_code = 0
    for n in names:
        try:
            declared = tracker_python_deps(cfg, n)
        except FileNotFoundError as e:
            typer.echo(f"{n}: {e}", err=True)
            exit_code = 1
            continue
        if not declared:
            typer.echo(f"{n}: no python_deps declared")
            continue
        typer.echo(f"{n}: installing {', '.join(declared)} -> {cfg.lib_dir}")
        try:
            result = install_tracker_deps(cfg, n)
        except DepsInstallError as e:
            typer.echo(f"{n}: {e}", err=True)
            exit_code = 1
            continue
        typer.echo(f"{n}: {result.detail}")
    if exit_code:
        raise typer.Exit(exit_code)


def setup(name: str | None = typer.Argument(None)) -> None:
    """Configure a tracker's required env vars / OAuth / FDA / instructions, then test sync.

    No argument → opens an interactive menu of all installed trackers.
    Argument     → runs setup for that one tracker and exits.
    """
    cfg = Config(root=get_root())
    if name is None:
        run_menu(cfg)
    else:
        result = run_tracker(cfg, name)
        if not result.success:
            raise typer.Exit(1)
