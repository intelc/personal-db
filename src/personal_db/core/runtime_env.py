"""Extend `sys.path` with `<root>/lib` at process startup.

Why this exists: the signed, notarized PersonalDB.app ships a *sealed*
embedded Python interpreter — its `site-packages` is inside the code-signed
bundle, and writing into it (e.g. to `pip install` a dependency a custom
tracker/app needs) would invalidate the signature. Custom packs still need a
place to put third-party dependencies the bundle doesn't carry, so
`<root>/lib` (`Config.lib_dir`) is that place: `personal-db tracker deps
<name>` (core/pack_deps.py) installs a pack's declared `python_deps` there,
and every long-lived entrypoint calls `activate_lib_dir` once at startup so
those installed packages are importable.

Ordering matters: a pack's dependency must never be able to shadow a
dependency the engine itself ships (accidentally or via a supply-chain
attack on a pack). We rely on `site.addsitedir` appending — not
prepending — both the directory itself and any paths it discovers via
`.pth` files in it to `sys.path` (see cpython `Lib/site.py`:
`addsitedir`/`addpackage` both call `sys.path.append`, never `insert`).
Because Python's import system resolves a module by scanning `sys.path` in
order and stopping at the first match, calling `activate_lib_dir` *after*
the interpreter has already populated `sys.path` with the stdlib and the
bundle's own `site-packages` guarantees `lib_dir` lands at the end of
`sys.path` — the bundle's own copy of a package always wins over a
same-named package a pack installed into `lib_dir`. This is verified in
tests/unit/test_runtime_env.py (an ordering test that installs a
same-named fake module into a fake "bundled site-packages" dir ahead of a
scratch lib_dir and asserts the bundled one is what gets imported).

`site.addsitedir` is also idempotent by construction: it computes the set of
paths already on `sys.path` before appending, so calling `activate_lib_dir`
more than once (every entrypoint calls it once at its own startup) does not
produce duplicate `sys.path` entries.
"""

from __future__ import annotations

import site
import sys
from pathlib import Path

from personal_db.core.config import Config


def resolve_app_bundle_root(path: Path) -> Path | None:
    """If `path` has a `<name>.app/Contents/...` segment, return the
    `<name>.app` root; else None.

    Pure path-string logic, deliberately not tied to `sys.executable` --
    shared by `is_app_bundle`/`app_bundle_cli` below (checking the running
    interpreter) and by `services/wizard/mcp_setup.py` (checking whether the
    `/usr/local/bin/personal-db` symlink resolves into a bundle), so both
    sides agree on what "is a bundle path" means.
    """
    parts = path.parts
    for i, part in enumerate(parts):
        if part.endswith(".app") and i + 1 < len(parts) and parts[i + 1] == "Contents":
            return Path(*parts[: i + 1])
    return None


def is_app_bundle() -> bool:
    """True when this process is running inside the signed PersonalDB.app.

    The Tauri shell's sidecar binary is spawned as
    `<bundle>/Contents/MacOS/personal-db-daemon`, so `sys.executable` lands
    inside an `.app/Contents/...` path. A dev virtualenv or `uv`-managed
    interpreter never has an `.app` path segment, so this is False there.
    Resolves symlinks first so a symlinked interpreter still matches.
    """
    try:
        exe = Path(sys.executable).resolve()
    except OSError:
        return False
    return resolve_app_bundle_root(exe) is not None


def app_bundle_cli() -> Path | None:
    """Path to the bundle's `Contents/Resources/cli/personal-db` CLI wrapper
    when running inside a packaged app bundle and the wrapper exists on
    disk, else None.

    This is the CLI entry point `services/wizard/mcp_setup.py` falls back to
    for writing absolute paths into MCP host configs when neither argv[0]
    nor the `/usr/local/bin/personal-db` symlink resolve usefully inside the
    sidecar (see that module's docstring) -- mirrors
    `shell/src-tauri/src/cli_install.rs::wrapper_path`.
    """
    if not is_app_bundle():
        return None
    exe = Path(sys.executable).resolve()
    root = resolve_app_bundle_root(exe)
    if root is None:  # pragma: no cover — is_app_bundle() already confirmed this
        return None
    cli = root / "Contents" / "Resources" / "cli" / "personal-db"
    return cli if cli.exists() else None


def activate_lib_dir(cfg: Config) -> bool:
    """Add `cfg.lib_dir` to `sys.path` (appended, after stdlib/site-packages)
    if it exists. No-op (returns False) if the directory doesn't exist yet —
    most installs never declare `python_deps`, so `<root>/lib` commonly never
    gets created. Returns True if the directory was (or already had been)
    activated.
    """
    lib_dir = cfg.lib_dir
    if not lib_dir.is_dir():
        return False
    site.addsitedir(str(lib_dir))
    return True
