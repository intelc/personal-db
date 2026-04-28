# Notes for Claude / agents working in this repo

## Editing a bundled tracker — required SOP

Bundled tracker templates live under `src/personal_db/templates/trackers/<name>/`. They are *templates*: when a user runs `personal-db tracker install <name>` (or the setup wizard installs them), the four canonical files (`manifest.yaml`, `ingest.py`, `schema.sql`, `visualizations.py`) are **copied** into `<root>/trackers/<name>/`.

`personal-db sync <name>` then runs the *installed copy*, not the template. So edits to `src/personal_db/templates/trackers/<name>/...` have no runtime effect on a system that already has that tracker installed until the installed copy is refreshed.

After every edit to a bundled template, propagate it:

```bash
personal-db --root ~/personal_db tracker reinstall <name>
```

`tracker reinstall` calls `installer.update_template()` (overwrites the four canonical files) and re-applies `schema.sql` so additive column changes land on the live DB. It preserves any non-canonical files in the tracker dir (cursor state, etc.).

If you also want a fresh historical resync after fixing an ingest parser:

```bash
# Clear the stored cursor so the next sync re-fetches the full backfill window
sqlite3 ~/personal_db/state/cursors.sqlite "DELETE FROM cursors WHERE name='<name>'"
personal-db --root ~/personal_db sync <name>
```

This pattern is what fixed the omi `structured.title` / `structured.category` parsing bug — the template was correct but the installed copy at `~/personal_db/trackers/omi/ingest.py` was stale.

## Where things live

- **Bundled templates:** `src/personal_db/templates/trackers/<name>/` — what ships with the package.
- **Installed copies:** `<root>/trackers/<name>/` (default `~/personal_db/trackers/<name>/`) — what `sync` actually executes.
- **Cursor state:** `<root>/state/cursors.sqlite` (table `cursors`, columns `name`, `value`).
- **Credentials:** `<root>/.env` (mode 0600, auto-loaded on every CLI invocation).
- **DB:** `<root>/db.sqlite` (single file, all tracker tables).

## Useful one-liners

```bash
# Validate a manifest parses cleanly
.venv/bin/python -c "from pathlib import Path; from personal_db.manifest import load_manifest; print(load_manifest(Path('src/personal_db/templates/trackers/<name>/manifest.yaml')))"

# Confirm a tracker is auto-discovered
.venv/bin/python -c "from personal_db.installer import list_bundled; print(list_bundled())"

# Tail the live UI log
tail -f /tmp/pdb-ui.log

# Run the relevant unit tests
.venv/bin/python -m pytest tests/unit/test_installer.py tests/unit/test_manifest.py tests/unit/test_smoke.py -q
```
