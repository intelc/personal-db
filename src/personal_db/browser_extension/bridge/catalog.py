"""Vetted app-owned connector catalog for the stable Chrome runtime.

Connector JavaScript is intentionally not accepted from socket callers.  The
catalog and sources are package resources supplied by the installed Personal DB
application; on a signed app installation that code-signing boundary is the
trust root.  This first production slice validates the declarative catalog and
source hash before forwarding a bundle internally to Chrome.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

MAX_SOURCE_BYTES = 250_000


class ConnectorCatalogError(ValueError):
    """The installed connector catalog is malformed or internally inconsistent."""


@dataclass(frozen=True)
class ConnectorBundle:
    id: str
    version: str
    runtime: int
    start_url: str
    run_at: str
    world: str
    result_global: str
    source: str
    sha256: str

    def native_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "runtime": self.runtime,
            "startUrl": self.start_url,
            "runAt": self.run_at,
            "world": self.world,
            "resultGlobal": self.result_global,
            "source": self.source,
            "sha256": self.sha256,
        }


_XHS_CREATOR_POLICY = {
    "id": "xhs.creator.v2",
    "runtime": 2,
    "start_url": "https://creator.xiaohongshu.com/new/note-manager",
    "run_at": "document_start",
    "world": "MAIN",
    "result_global": "__personalDbXhsCreatorApiTap",
}


def _safe_source_name(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith(".js"):
        raise ConnectorCatalogError("connector source must be a JavaScript filename")
    path = PurePosixPath(value)
    if path.name != value or path.name.startswith(".") or ".." in path.parts:
        raise ConnectorCatalogError("connector source path is not a safe filename")
    return value


def _catalog_root() -> Any:
    return resources.files("personal_db.browser_extension.connectors").joinpath("xhs")


def _catalog_entry(connector_id: str) -> tuple[dict[str, Any], Any]:
    if connector_id != _XHS_CREATOR_POLICY["id"]:
        raise ConnectorCatalogError("unknown connector id")
    root = _catalog_root()
    try:
        catalog = json.loads(root.joinpath("catalog.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectorCatalogError("cannot read XHS connector catalog") from exc
    if not isinstance(catalog, dict) or catalog.get("catalog_version") != 1 or catalog.get("runtime") != 2:
        raise ConnectorCatalogError("unsupported XHS connector catalog version")
    connectors = catalog.get("connectors")
    if not isinstance(connectors, list):
        raise ConnectorCatalogError("connector catalog has no connector list")
    matches = [entry for entry in connectors if isinstance(entry, dict) and entry.get("id") == connector_id]
    if len(matches) != 1:
        raise ConnectorCatalogError("connector catalog has no unique matching connector")
    return matches[0], root


def load_connector_bundle(connector_id: str) -> ConnectorBundle:
    """Load one package-owned connector after strict policy/hash validation."""
    entry, root = _catalog_entry(connector_id)
    expected_fields = {
        "id", "version", "runtime", "start_url", "run_at", "world",
        "result_global", "source", "sha256",
    }
    if set(entry) != expected_fields:
        raise ConnectorCatalogError("connector catalog entry contains unsupported fields")
    for name, expected in _XHS_CREATOR_POLICY.items():
        if entry.get(name) != expected:
            raise ConnectorCatalogError(f"connector catalog violates the baked {name} policy")
    if not isinstance(entry["version"], str) or not entry["version"]:
        raise ConnectorCatalogError("connector version is invalid")
    source_name = _safe_source_name(entry["source"])
    declared_hash = entry.get("sha256")
    if not isinstance(declared_hash, str) or len(declared_hash) != 64 or any(c not in "0123456789abcdef" for c in declared_hash):
        raise ConnectorCatalogError("connector sha256 is invalid")
    try:
        source_bytes = root.joinpath(source_name).read_bytes()
    except OSError as exc:
        raise ConnectorCatalogError("connector source is unavailable") from exc
    if not source_bytes:
        raise ConnectorCatalogError("connector source is empty")
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise ConnectorCatalogError("connector source exceeds the maximum size")
    if hashlib.sha256(source_bytes).hexdigest() != declared_hash:
        raise ConnectorCatalogError("connector source sha256 does not match catalog")
    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConnectorCatalogError("connector source is not UTF-8") from exc
    return ConnectorBundle(
        id=entry["id"], version=entry["version"], runtime=entry["runtime"],
        start_url=entry["start_url"], run_at=entry["run_at"], world=entry["world"],
        result_global=entry["result_global"], source=source, sha256=declared_hash,
    )
