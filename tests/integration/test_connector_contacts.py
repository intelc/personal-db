"""Tests for the contacts tracker + its optional join with imessage."""

import sqlite3
import subprocess
import sys
from pathlib import Path

from personal_db.config import Config
from personal_db.handle_norm import handle_kind, normalize_handle
from personal_db.sync import sync_one


def _seed_addressbook(root: Path, *, source: str = "src1",
                      records: list[tuple] = (),  # type: ignore
                      phones: list[tuple] = (),
                      emails: list[tuple] = ()):
    """Build a fake AddressBook source DB at root/Sources/<source>/AddressBook-v22.abcddb."""
    db_path = root / "Sources" / source / "AddressBook-v22.abcddb"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE ZABCDRECORD (
            Z_PK INTEGER PRIMARY KEY,
            ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZNICKNAME TEXT,
            ZORGANIZATION TEXT, ZMODIFICATIONDATE REAL
        );
        CREATE TABLE ZABCDPHONENUMBER (
            Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT
        );
        CREATE TABLE ZABCDEMAILADDRESS (
            Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO ZABCDRECORD(Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, "
        "ZORGANIZATION, ZMODIFICATIONDATE) VALUES (?, ?, ?, ?, ?, ?)",
        records,
    )
    con.executemany(
        "INSERT INTO ZABCDPHONENUMBER(ZOWNER, ZFULLNUMBER) VALUES (?, ?)",
        phones,
    )
    con.executemany(
        "INSERT INTO ZABCDEMAILADDRESS(ZOWNER, ZADDRESS) VALUES (?, ?)",
        emails,
    )
    con.commit()
    con.close()
    return db_path


def _install(tmp_path, *trackers):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True, capture_output=True,
    )
    for t in trackers:
        subprocess.run(
            [sys.executable, "-m", "personal_db.cli.main", "--root", str(root),
             "tracker", "install", t],
            check=True, capture_output=True,
        )
    return Config(root=root)


def test_normalize_handle_phone_and_email():
    # Phone: digits-only, last 10
    assert normalize_handle("+14089215283") == "4089215283"
    assert normalize_handle("(408) 921-5283") == "4089215283"
    assert normalize_handle("4089215283") == "4089215283"
    # Short numbers: full digits
    assert normalize_handle("12345") == "12345"
    # UK number — last 10 digits
    assert normalize_handle("+447961107995") == "7961107995"
    # Email: lowercase, stripped
    assert normalize_handle("  Foo@Example.COM ") == "foo@example.com"
    # Empty
    assert normalize_handle(None) == ""
    assert normalize_handle("") == ""


def test_handle_kind():
    assert handle_kind("foo@bar.com") == "email"
    assert handle_kind("+14089215283") == "phone"
    assert handle_kind(None) == "phone"


def test_contacts_sync_reads_addressbook(tmp_path, monkeypatch):
    cfg = _install(tmp_path, "contacts")
    ab_root = tmp_path / "addressbook"
    _seed_addressbook(
        ab_root,
        source="abc-uuid",
        records=[
            (1, "Alice", "Smith", None, None, 7.6e8),
            (2, "Bob", "Jones", "Bobby", "Acme Corp", 7.7e8),
            (3, None, None, None, "Org Only LLC", 7.8e8),
        ],
        phones=[
            (1, "+1 (408) 921-5283"),
            (1, "408-555-1212"),
            (2, "+447961107995"),
        ],
        emails=[
            (1, "alice@example.com"),
            (2, "Bob@ACME.com"),
        ],
    )
    monkeypatch.setenv("CONTACTS_ADDRESSBOOK_DIR", str(ab_root))
    sync_one(cfg, "contacts")

    con = sqlite3.connect(cfg.db_path)
    contacts = con.execute(
        "SELECT contact_id, full_name, display_name, organization "
        "FROM contacts ORDER BY contact_id"
    ).fetchall()
    handles = con.execute(
        "SELECT contact_id, kind, normalized, raw FROM contact_handles "
        "ORDER BY contact_id, kind, normalized"
    ).fetchall()
    con.close()

    # 3 contacts (Alice, Bob, org-only)
    assert len(contacts) == 3
    by_id = {c[0]: c for c in contacts}
    assert by_id["abc-uuid:1"] == ("abc-uuid:1", "Alice Smith", "Alice Smith", "")
    # Org-only contact: display_name falls back to organization
    assert by_id["abc-uuid:3"][2] == "Org Only LLC"

    # 5 handles total
    assert len(handles) == 5
    # Alice's two phones normalize correctly
    alice_phones = sorted(h[2] for h in handles if h[0] == "abc-uuid:1" and h[1] == "phone")
    assert alice_phones == ["4085551212", "4089215283"]
    # Bob's email is lowercased
    bob_emails = [h for h in handles if h[0] == "abc-uuid:2" and h[1] == "email"]
    assert bob_emails == [("abc-uuid:2", "email", "bob@acme.com", "Bob@ACME.com")]


def test_contacts_resync_replaces_old_data(tmp_path, monkeypatch):
    """Snapshot semantics: a second sync overwrites. Renames apply."""
    cfg = _install(tmp_path, "contacts")
    ab_root = tmp_path / "addressbook"
    _seed_addressbook(
        ab_root, source="s1",
        records=[(1, "Alice", "Smith", None, None, 7.6e8)],
        phones=[(1, "+14089215283")],
    )
    monkeypatch.setenv("CONTACTS_ADDRESSBOOK_DIR", str(ab_root))
    sync_one(cfg, "contacts")

    # Now wipe + reseed with a renamed contact and a new phone
    import shutil
    shutil.rmtree(ab_root / "Sources" / "s1")
    _seed_addressbook(
        ab_root, source="s1",
        records=[(1, "Alice", "Renamed", None, None, 7.7e8)],
        phones=[(1, "+15555555555")],
    )
    sync_one(cfg, "contacts")

    con = sqlite3.connect(cfg.db_path)
    name = con.execute("SELECT display_name FROM contacts").fetchone()[0]
    handles = con.execute("SELECT normalized FROM contact_handles").fetchall()
    con.close()
    assert name == "Alice Renamed"
    assert [h[0] for h in handles] == ["5555555555"]


def test_imessage_top_contacts_resolves_via_contacts(tmp_path, monkeypatch):
    """The imessage:top_contacts viz should use contacts when both are synced."""
    cfg = _install(tmp_path, "contacts", "imessage")
    ab_root = tmp_path / "addressbook"
    _seed_addressbook(
        ab_root, source="s1",
        records=[
            (1, "Alice", "Smith", None, None, 7.6e8),
            (2, "Bob", "Jones", None, None, 7.7e8),
        ],
        phones=[
            (1, "+14089215283"),
            (2, "+447961107995"),
        ],
        emails=[(2, "bob@example.com")],
    )
    monkeypatch.setenv("CONTACTS_ADDRESSBOOK_DIR", str(ab_root))
    sync_one(cfg, "contacts")

    # Seed iMessage messages — note Alice's handle uses the +1 form with
    # parens; Bob's is bare digits with country code; one unknown handle too.
    today = "2026-04-26"
    con = sqlite3.connect(cfg.db_path)
    con.executemany(
        "INSERT INTO imessage_messages(person_id, handle, text, is_from_me, sent_at) "
        "VALUES (NULL, ?, ?, 0, ?)",
        [
            ("+1 (408) 921-5283", "hi from alice 1", today),
            ("4089215283", "hi from alice 2", today),
            ("+447961107995", "hi from bob via uk #", today),
            ("bob@example.com", "hi from bob via email", today),
            ("+15555550000", "hi stranger", today),
        ],
    )
    con.commit()
    con.close()

    from personal_db.ui.viz import discover
    reg = discover(cfg)
    html = reg["imessage:top_contacts_30d"].render(cfg)
    # Both Alice's handles roll up into "Alice Smith" (count=2)
    assert "Alice Smith" in html
    # Bob's phone + email both resolve to "Bob Jones" (count=2)
    assert "Bob Jones" in html
    # Unknown handle falls through as raw
    assert "+15555550000" in html
    # Coverage marker present (4 of 5 messages resolved = 80%)
    assert "80%" in html


def test_imessage_top_contacts_falls_back_when_contacts_not_installed(tmp_path):
    """Without contacts tracker, viz should still work — just shows raw handles."""
    cfg = _install(tmp_path, "imessage")
    today = "2026-04-26"
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO imessage_messages(person_id, handle, text, is_from_me, sent_at) "
        "VALUES (NULL, '+14089215283', 'hi', 0, ?)", (today,),
    )
    con.commit()
    con.close()
    from personal_db.ui.viz import discover
    reg = discover(cfg)
    html = reg["imessage:top_contacts_30d"].render(cfg)
    # Raw handle visible; fallback hint mentions contacts tracker
    assert "+14089215283" in html
    assert "contacts" in html.lower()
