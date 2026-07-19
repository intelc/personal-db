#!/usr/bin/env python3
"""Propagate the canonical version (pyproject.toml [project] version) into
the two places the Tauri shell mirrors it:

  shell/src-tauri/tauri.conf.json   "version" field
  shell/src-tauri/Cargo.toml        [package] version

Stdlib only, idempotent, prints exactly what changed (or that nothing did).

Usage:
    scripts/sync-version.py            # write mode: fix drift, report
    scripts/sync-version.py --check    # CI mode: exit 1 on drift, write nothing
    scripts/sync-version.py --root X   # operate on a different repo root
                                       # (used by the unit tests)

Edits are targeted regex substitutions (not json/toml round-trips) so the
files' existing formatting and comments survive untouched.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def canonical_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    try:
        version = data["project"]["version"]
    except KeyError:
        sys.exit(f"error: no [project] version in {pyproject}")
    if not isinstance(version, str) or not version:
        sys.exit(f"error: [project] version in {pyproject} is not a non-empty string")
    return version


def sync_tauri_conf(path: Path, version: str) -> tuple[str | None, str]:
    """Returns (old_version_or_None_if_in_sync, new_text)."""
    text = path.read_text()
    # The top-level "version" key. tauri.conf.json keeps it near the top of
    # the root object; match the first occurrence, which is the app version
    # (nested objects like plugins.updater carry no "version" key).
    pattern = re.compile(r'("version"\s*:\s*")([^"]+)(")')
    m = pattern.search(text)
    if not m:
        sys.exit(f'error: no "version" field found in {path}')
    old = m.group(2)
    if old == version:
        return None, text
    new_text = pattern.sub(lambda mm: mm.group(1) + version + mm.group(3), text, count=1)
    return old, new_text


def sync_cargo_toml(path: Path, version: str) -> tuple[str | None, str]:
    """Returns (old_version_or_None_if_in_sync, new_text)."""
    text = path.read_text()
    # `version = "..."` inside the [package] section: match the first
    # `version =` line after `[package]` and before the next section header.
    pattern = re.compile(
        r'(^\[package\][^\[]*?^version\s*=\s*")([^"]+)(")',
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        sys.exit(f"error: no [package] version found in {path}")
    old = m.group(2)
    if old == version:
        return None, text
    new_text = pattern.sub(lambda mm: mm.group(1) + version + mm.group(3), text, count=1)
    return old, new_text


def sync_cargo_lock(path: Path, version: str) -> tuple[str | None, str]:
    """Returns (old_version_or_None_if_in_sync, new_text).

    Cargo only rewrites the lockfile on its next invocation, so a version
    bump that stops at Cargo.toml leaves the tree dirty mid-release (the
    preflight clean-tree check caught exactly this on v0.1.2). Pin the
    personal-db-shell entry here too.
    """
    text = path.read_text()
    pattern = re.compile(
        r'(^name = "personal-db-shell"\nversion = ")([^"]+)(")',
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        sys.exit(f"error: no personal-db-shell package entry found in {path}")
    old = m.group(2)
    if old == version:
        return None, text
    new_text = pattern.sub(lambda mm: mm.group(1) + version + mm.group(3), text, count=1)
    return old, new_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any mirrored version drifts from pyproject.toml; write nothing",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repo root to operate on (default: this script's repo)",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    version = canonical_version(root)

    targets = [
        (root / "shell" / "src-tauri" / "tauri.conf.json", sync_tauri_conf),
        (root / "shell" / "src-tauri" / "Cargo.toml", sync_cargo_toml),
        (root / "shell" / "src-tauri" / "Cargo.lock", sync_cargo_lock),
    ]

    drift = False
    for path, sync in targets:
        if not path.exists():
            sys.exit(f"error: {path} not found")
        old, new_text = sync(path, version)
        rel = path.relative_to(root)
        if old is None:
            print(f"{rel}: {version} (in sync)")
            continue
        drift = True
        if args.check:
            print(f"{rel}: {old} != {version} (DRIFT)")
        else:
            path.write_text(new_text)
            print(f"{rel}: {old} -> {version}")

    if args.check and drift:
        print("version drift detected; run scripts/sync-version.py to fix", file=sys.stderr)
        return 1
    if not drift:
        print(f"all versions in sync at {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
