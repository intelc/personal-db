"""Read/write/update <root>/.env preserving comments and ordering.

Atomic writes (write tmp + rename) so partial-write corruption is impossible.
No external dependency — handwritten parser/writer for portability.
"""

from __future__ import annotations

import os
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    """Parse a .env file. Returns {} if file is missing."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = _strip_quotes(v.strip())
    return out


def upsert_env(path: Path, key: str, value: str) -> None:
    """Insert or update a single key in .env, preserving existing structure.

    - File created with mode 0600 if missing.
    - Comments and blank lines preserved.
    - Atomic write via tmp + rename.
    """
    lines = path.read_text().splitlines() if path.exists() else []
    formatted = f"{key}={_quote_if_needed(value)}"
    replaced = False
    new_lines: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if (
            stripped
            and not stripped.startswith("#")
            and "=" in stripped
            and stripped.split("=", 1)[0].strip() == key
        ):
            new_lines.append(formatted)
            replaced = True
        else:
            new_lines.append(raw)
    if not replaced:
        new_lines.append(formatted)
    body = "\n".join(new_lines) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _quote_if_needed(value: str) -> str:
    if any(ch in value for ch in (" ", "\t", "#", '"')):
        # double-quote and escape any embedded double quotes
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value
