"""Dynamic module/entrypoint loading shared by extension registries and routes.

An "entrypoint" string is ``"<module_file>:<function>"``, resolved relative to
an extension's installed directory (a tracker dir, app dir, or source dir).
This is the same dynamic-load pattern ``services/daemon/routes/actions.py``
already used inline for tracker/app actions; it lives in core so both the
background-job scheduler and the MCP tool registry (see
``core.background_jobs`` / ``core.mcp_registry``) can reuse it without
services importing each other's internals.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any


def load_module_from_file(path: Path, modname: str) -> Any:
    """Dynamically load a Python module from `path` under `modname`.

    Reads and compiles the source directly (rather than going through
    ``importlib.util.spec_from_file_location``) so it never serves stale
    ``__pycache__`` bytecode: the standard source loader validates its cache
    by mtime+size, which can both be identical across two rapid edits to a
    small file (same content length, same filesystem-mtime second) and would
    silently execute the *previous* version. Background jobs and MCP tools
    reload their entrypoint module on every invocation specifically so
    edits take effect without a daemon/MCP-server restart, so staleness here
    would be a confusing, hard-to-diagnose bug for extension authors.

    Registers the module in ``sys.modules`` under `modname` *before*
    executing it so relative imports inside the module resolve correctly, and
    drops any previously loaded module under the same name first.
    """
    sys.modules.pop(modname, None)
    source = path.read_text()
    code = compile(source, str(path), "exec")
    module = types.ModuleType(modname)
    module.__file__ = str(path)
    sys.modules[modname] = module
    try:
        exec(code, module.__dict__)  # noqa: S102 — trusted local extension file, not user input
    except Exception:
        sys.modules.pop(modname, None)
        raise
    return module


def load_entrypoint(
    base_dir: Path,
    entrypoint: str,
    *,
    modname_prefix: str,
) -> Callable[..., Any]:
    """Resolve a declared ``"<module_file>:<function>"`` entrypoint.

    `base_dir` is the extension's installed directory (tracker/app/source
    dir). Raises ValueError/FileNotFoundError/AttributeError with a clear
    message on any resolution failure.
    """
    module_file, sep, func_name = entrypoint.partition(":")
    if not sep or not module_file or not func_name:
        raise ValueError(
            f"invalid entrypoint {entrypoint!r}; expected '<module_file>:<function>'"
        )
    path = base_dir / f"{module_file}.py"
    if not path.is_file():
        raise FileNotFoundError(f"entrypoint module not found: {path}")
    module = load_module_from_file(path, f"{modname_prefix}_{module_file}")
    func = getattr(module, func_name, None)
    if func is None or not callable(func):
        raise AttributeError(f"entrypoint {entrypoint!r}: {func_name!r} not found or not callable")
    return func
