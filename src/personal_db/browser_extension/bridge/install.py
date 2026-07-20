"""Install the Chrome native-messaging manifest for the XHS collector."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import stat
import sys
from pathlib import Path

HOST_NAME = "com.personaldb.xhs_collector"


def extension_dir() -> Path:
    """Return the Chrome-loadable assets, excluding the Python bridge package."""
    return Path(__file__).resolve().parents[1] / "chrome"


def extension_id() -> str:
    manifest = json.loads((extension_dir() / "manifest.json").read_text())
    key = manifest.get("key")
    if not isinstance(key, str) or not key:
        raise RuntimeError("extension manifest must contain a stable public key")
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(nibble, 16)) for nibble in digest)


def _chrome_host_dir() -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("Personal DB's XHS collector installer currently supports Chrome on macOS")
    return Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts"


def install_native_host(root: Path) -> dict[str, Path | str]:
    """Write a root-scoped launcher and Chrome's narrowly-scoped host manifest."""
    root = root.expanduser().resolve()
    state_dir = root / "state"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    socket_path = state_dir / "browser-collector.sock"
    launcher = state_dir / "browser-collector-host"
    launcher.write_text(
        "#!/bin/sh\n"
        f"export PDB_BROWSER_BRIDGE_SOCK={shlex.quote(str(socket_path))}\n"
        f"exec {shlex.quote(sys.executable)} -m personal_db.browser_extension.bridge.host\n"
    )
    launcher.chmod(0o700)

    host_dir = _chrome_host_dir()
    host_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = host_dir / f"{HOST_NAME}.json"
    manifest = {
        "name": HOST_NAME,
        "description": "Personal DB XHS collector bridge",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id()}/"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return {
        "extension_dir": extension_dir(),
        "extension_id": extension_id(),
        "socket": socket_path,
        "launcher": launcher,
        "host_manifest": manifest_path,
    }
