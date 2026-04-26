"""Contacts connector — reads macOS AddressBook SQLite DBs.

AddressBook v22 stores each "source" (iCloud, Google, On My Mac, Exchange)
in its own DB at:
  ~/Library/Application Support/AddressBook/Sources/<UUID>/AddressBook-v22.abcddb

There's also a top-level db at:
  ~/Library/Application Support/AddressBook/AddressBook-v22.abcddb
which we read only if no per-source DBs are found (the top-level often
duplicates the source data).

Schema columns of interest (all on the Z* CoreData prefix):
  ZABCDRECORD:        Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION,
                      ZMODIFICATIONDATE
  ZABCDPHONENUMBER:   ZOWNER (FK to ZABCDRECORD.Z_PK), ZFULLNUMBER
  ZABCDEMAILADDRESS:  ZOWNER (FK), ZADDRESS

ZMODIFICATIONDATE is Mac absolute time (seconds since 2001-01-01 UTC).

Sync strategy: snapshot. On each run we wipe both tables and re-insert from
the source — contacts data is small (typically <10K) and fully derivable, so
incremental sync isn't worth the complexity.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_db.handle_norm import normalize_handle
from personal_db.tracker import Tracker

_DB_NAME = "AddressBook-v22.abcddb"
_MAC_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


def _addressbook_root() -> Path:
    return Path(
        os.environ.get("CONTACTS_ADDRESSBOOK_DIR")
        or "~/Library/Application Support/AddressBook"
    ).expanduser()


def _find_source_dbs() -> list[tuple[str, Path]]:
    """Returns [(source_label, db_path), ...].

    Prefers per-source DBs under Sources/<UUID>/. Falls back to the top-level
    DB if no sources exist. The source_label becomes part of contact_id so
    PKs from different sources don't collide.
    """
    out: list[tuple[str, Path]] = []
    root = _addressbook_root()
    sources_dir = root / "Sources"
    # iterdir() can raise PermissionError on macOS even when is_dir() succeeds
    # (Apple's TCC sometimes lets stat-style probes through but blocks readdir).
    # Treat that the same as "no sources" and fall through to the top-level DB.
    try:
        if sources_dir.is_dir():
            for child in sorted(sources_dir.iterdir()):
                db = child / _DB_NAME
                if db.is_file():
                    out.append((child.name, db))
    except PermissionError:
        pass
    if not out:
        top = root / _DB_NAME
        try:
            if top.is_file():
                out.append(("main", top))
        except PermissionError:
            pass
    return out


def _mac_time_to_iso(seconds_since_2001: float | None) -> str | None:
    if seconds_since_2001 is None:
        return None
    try:
        return (_MAC_EPOCH + timedelta(seconds=float(seconds_since_2001))).isoformat()
    except (ValueError, TypeError):
        return None


def _display_name(first: str | None, last: str | None,
                  nickname: str | None, organization: str | None) -> str:
    full = " ".join(p for p in (first, last) if p)
    if full:
        return full
    if nickname:
        return nickname
    if organization:
        return organization
    return "(unnamed)"


def _read_source(source: str, db_path: Path) -> tuple[list[dict], list[dict]]:
    """Pull contacts + handles from one AddressBook DB.

    Uses mode=ro&immutable=1 to bypass any locking (Contacts.app may be open).
    """
    contacts: list[dict] = []
    handles: list[dict] = []
    con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        # Records — ignore deleted ones (records have ZDELETED on some columns)
        rec_rows = con.execute(
            "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION, "
            "       ZMODIFICATIONDATE "
            "FROM ZABCDRECORD"
        ).fetchall()
        record_pks: set[int] = set()
        for pk, first, last, nick, org, mtime in rec_rows:
            if not (first or last or nick or org):
                # Skip wholly-empty records (link/relationship stubs)
                continue
            record_pks.add(pk)
            contact_id = f"{source}:{pk}"
            contacts.append({
                "contact_id": contact_id,
                "full_name": " ".join(p for p in (first, last) if p) or "",
                "display_name": _display_name(first, last, nick, org),
                "organization": org or "",
                "source": source,
                "updated_at": _mac_time_to_iso(mtime) or datetime.now(UTC).isoformat(),
            })

        # Phones
        for pk, full_number in con.execute(
            "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL"
        ):
            if pk not in record_pks:
                continue
            normalized = normalize_handle(full_number)
            if not normalized:
                continue
            handles.append({
                "contact_id": f"{source}:{pk}",
                "kind": "phone",
                "normalized": normalized,
                "raw": full_number,
            })

        # Emails
        for pk, address in con.execute(
            "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL"
        ):
            if pk not in record_pks:
                continue
            normalized = normalize_handle(address)
            if not normalized:
                continue
            handles.append({
                "contact_id": f"{source}:{pk}",
                "kind": "email",
                "normalized": normalized,
                "raw": address,
            })
    finally:
        con.close()
    return contacts, handles


def sync(t: Tracker) -> None:
    dbs = _find_source_dbs()
    if not dbs:
        t.log.info("contacts: no AddressBook DBs found at %s", _addressbook_root())
        return

    all_contacts: list[dict] = []
    all_handles: list[dict] = []
    for source, db_path in dbs:
        try:
            contacts, handles = _read_source(source, db_path)
        except (sqlite3.Error, OSError) as e:
            t.log.warning("contacts: skipping %s (%s): %s", source, db_path, e)
            continue
        all_contacts.extend(contacts)
        all_handles.extend(handles)

    if not all_contacts:
        # Don't wipe what we already have if the sync produced nothing — protects
        # the table from a transient TCC denial or a temporarily-locked source.
        t.log.warning(
            "contacts: no contacts read from %d source DB(s); leaving existing rows in place",
            len(dbs),
        )
        return

    # Snapshot: wipe and re-insert. Contacts data is small and fully derivable.
    con = sqlite3.connect(t.cfg.db_path)
    try:
        con.execute("DELETE FROM contact_handles")
        con.execute("DELETE FROM contacts")
        con.commit()
    finally:
        con.close()

    t.upsert("contacts", all_contacts, key=["contact_id"])
    if all_handles:
        t.upsert("contact_handles", all_handles, key=["contact_id", "kind", "normalized"])

    t.log.info(
        "contacts: synced %d contacts and %d handles across %d source DB(s)",
        len(all_contacts), len(all_handles), len(dbs),
    )


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)
