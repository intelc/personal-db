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

from personal_db.core.config import Config


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
