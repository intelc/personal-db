from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from personal_db.config import Config
from personal_db.remote_sources.base import RemoteCallResult
from personal_db.sources import SourceManifestError, get_source_definition

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

_ACCOUNT_RE = re.compile(r"^Email Account:\s+(?P<email>\S+)(?:\s+\(Access:\s+(?P<access>[^)]+)\))?")
_FOLDER_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+(?P<count>\d+)\s+messages\s+\((?P<identifier>.+)\)\s*$"
)
_TOTAL_RE = re.compile(r"^\s*Total\s+(?P<count>\d+)\s+messages\s*$")
_PAGE_RE = re.compile(
    r"Page\s+(?P<page>\d+)\s+of\s+(?P<pages>\d+)\s+\((?P<total>\d+)\s+total emails\)"
)
_EMAIL_ID_RE = re.compile(r"^\s*(?P<id>\d+)\s+")
_SEARCH_ID_RE = re.compile(r"^\s*ID:\s*(?P<id>\d+)\s*$")


@dataclass(frozen=True)
class SparkCommandError(RuntimeError):
    command_args: tuple[str, ...]
    returncode: int
    stderr: str

    def __str__(self) -> str:
        msg = self.stderr.strip() or f"spark exited with {self.returncode}"
        return f"spark {' '.join(self.command_args)} failed: {msg}"


class SparkSourceConfigError(ValueError):
    pass


class SparkEmailSource:
    """Live email source backed by the Spark Desktop CLI.

    Spark must be installed and Spark Desktop must be running. This source does
    not persist email data; it exposes Spark's account, folder, search, and
    thread operations for context providers and future enrichment jobs.
    """

    name = "spark_email"

    def __init__(
        self,
        *,
        command: str = "spark",
        runner: Runner | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.command = command
        self._runner = runner
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_config(
        cls,
        cfg: Config,
        *,
        require_installed: bool = False,
        runner: Runner | None = None,
    ) -> SparkEmailSource:
        try:
            definition = get_source_definition(
                cfg,
                "spark_email",
                include_bundled=not require_installed,
            )
        except FileNotFoundError as exc:
            raise SparkSourceConfigError(
                "spark_email source is not installed. Run "
                "`personal-db source install spark_email`."
            ) from exc
        manifest = definition.manifest
        if manifest.provider != "spark":
            raise SparkSourceConfigError(
                f"spark_email source has unsupported provider: {manifest.provider}"
            )
        if not manifest.enabled:
            raise SparkSourceConfigError("spark_email source is disabled in source.yaml")
        timeout = manifest.config.get("timeout_seconds", 60)
        try:
            timeout_seconds = int(timeout)
        except (TypeError, ValueError) as exc:
            raise SourceManifestError("spark_email config.timeout_seconds must be an integer") from exc
        return cls(command=manifest.command or "spark", runner=runner, timeout_seconds=timeout_seconds)

    def check(self) -> RemoteCallResult:
        path = shutil.which(self.command)
        data: dict[str, Any] = {"available": path is not None, "command": self.command}
        raw = ""
        if path is not None:
            data["path"] = path
            try:
                proc = self._run(["--version"], check=False)
                raw = (proc.stdout or proc.stderr).strip()
                data["version"] = raw
                data["ok"] = proc.returncode == 0
            except Exception as e:
                data["ok"] = False
                data["error"] = str(e)
        else:
            data["ok"] = False
            data["error"] = f"{self.command!r} not found on PATH"
        return RemoteCallResult(self.name, "check", raw, data)

    def accounts(self) -> RemoteCallResult:
        raw = self._spark(["accounts"])
        accounts = []
        for line in raw.splitlines():
            match = _ACCOUNT_RE.match(line.strip())
            if match:
                accounts.append(match.groupdict())
        return RemoteCallResult(self.name, "accounts", raw, {"accounts": accounts})

    def folders(self, scope: str | None = None) -> RemoteCallResult:
        args = ["folders"]
        if scope:
            args.append(scope)
        raw = self._spark(args)
        return RemoteCallResult(self.name, "folders", raw, _parse_folders(raw))

    def emails(
        self,
        *,
        folders: Sequence[str] | None = None,
        filter_: str | None = None,
        page: int = 1,
        page_size: int = 50,
        order: str | None = None,
        new_senders: bool = False,
    ) -> RemoteCallResult:
        args = ["emails"]
        if filter_:
            args.extend(["--filter", filter_])
        args.extend(["--page", str(page), "--page-size", str(page_size)])
        if order:
            args.extend(["--order", order])
        if new_senders:
            args.append("--new-senders")
        if folders:
            args.extend(folders)
        raw = self._spark(args)
        return RemoteCallResult(self.name, "emails", raw, _parse_email_listing(raw))

    def search(
        self,
        about: str,
        *,
        filter_: str | None = None,
        in_: str | None = None,
    ) -> RemoteCallResult:
        args = ["search"]
        if filter_:
            args.extend(["--filter", filter_])
        if in_:
            args.extend(["--in", in_])
        args.append(about)
        raw = self._spark(args)
        return RemoteCallResult(self.name, "search", raw, _parse_email_listing(raw))

    def thread(self, message_id: str, *, download_attachments: bool = False) -> RemoteCallResult:
        args = ["thread"]
        if download_attachments:
            args.append("--download-attachments")
        args.append(str(message_id))
        raw = self._spark(args)
        return RemoteCallResult(
            self.name,
            "thread",
            raw,
            {"message_id": str(message_id), "download_attachments": download_attachments},
        )

    def _spark(self, args: Sequence[str]) -> str:
        proc = self._run(args, check=True)
        return proc.stdout

    def _run(
        self,
        args: Sequence[str],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        if self._runner is not None:
            proc = self._runner([self.command, *args])
        else:
            proc = subprocess.run(
                [self.command, *args],
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout_seconds,
            )
        if check and proc.returncode != 0:
            raise SparkCommandError(tuple(args), proc.returncode, proc.stderr)
        return proc


def _parse_folders(raw: str) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "Unified":
            current = {"kind": "unified", "name": "Unified", "folders": []}
            groups.append(current)
            continue
        if stripped.startswith("Email Account:"):
            name = stripped.removeprefix("Email Account:").strip()
            gmail_labels = "(Gmail labels)" in name
            name = name.replace("(Gmail labels)", "").strip()
            current = {
                "kind": "account",
                "name": name,
                "gmail_labels": gmail_labels,
                "folders": [],
            }
            groups.append(current)
            continue
        if set(stripped) <= {"-"}:
            continue
        if current is None:
            continue
        total_match = _TOTAL_RE.match(line)
        if total_match:
            current["total"] = int(total_match.group("count"))
            continue
        folder_match = _FOLDER_RE.match(line)
        if folder_match:
            folder = folder_match.groupdict()
            folder["count"] = int(folder["count"])
            current["folders"].append(folder)

    return {"groups": groups}


def _parse_email_listing(raw: str) -> dict[str, Any]:
    page = None
    email_ids: list[str] = []
    for line in raw.splitlines():
        page_match = _PAGE_RE.search(line)
        if page_match:
            page = {k: int(v) for k, v in page_match.groupdict().items()}
            continue
        search_id_match = _SEARCH_ID_RE.match(line)
        if search_id_match:
            email_ids.append(search_id_match.group("id"))
            continue
    if not email_ids:
        for line in raw.splitlines():
            if " result(s)" in line:
                continue
            if line.lstrip().startswith(("ID", "Page ")):
                continue
            if id_match := _EMAIL_ID_RE.match(line):
                email_ids.append(id_match.group("id"))
    email_ids = list(dict.fromkeys(email_ids))
    data: dict[str, Any] = {"email_ids": email_ids}
    if page:
        data["page"] = page
    return data
