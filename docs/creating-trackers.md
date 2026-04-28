# Creating a tracker

A *tracker* (sometimes called a *connector*) is a self-contained module that pulls data from one source — a local file, an HTTP API, an OS database, manual logging — into your personal_db. Trackers are the unit of extension: anything you want personal_db to remember becomes a tracker.

## Three ways to create one

### 1. Ask Claude (recommended)

The fastest path. Once `personal-db setup` has wired the MCP server into a Claude client (Code, Desktop, or Cursor), open a new conversation and ask:

> "Use the `create_tracker` prompt to design a tracker that captures my Apple Notes."

The prompt walks Claude through a Q&A — what the tracker captures, what columns the table should have, what the time column is, how often to sync, what permissions are needed. Claude then writes the four files into your local `~/personal_db/trackers/<name>/` and validates the manifest against the framework's schema. You install it with one final command.

This route matches how most users will create trackers: describe what you want, let the agent do the typing.

### 2. Scaffold via CLI

```bash
personal-db tracker new my_tracker
```

Drops a stub at `~/personal_db/trackers/my_tracker/` with a starter `manifest.yaml`, `schema.sql`, and `ingest.py`. Edit them by hand. Useful when you already know what you want and just need the boilerplate.

### 3. Copy an existing tracker

The bundled trackers under `src/personal_db/templates/trackers/` are real, working examples. Pick the closest one to your use case and adapt:

| Source type           | Closest example          |
|-----------------------|--------------------------|
| Local SQLite database | `chrome_history`, `imessage`, `screen_time` |
| JSONL files on disk   | `claude_conversations`, `codex_conversations` |
| HTTP API (no auth)    | (none yet — adapt one of the OAuth ones) |
| HTTP API + OAuth      | `whoop`                  |
| HTTP API + API key    | `github_commits`         |
| macOS Address Book    | `contacts`               |
| Manual logging        | `habits`, `life_context` |
| Derived (no source)   | `daily_time_accounting`, `project_time` |

## Anatomy of a tracker

A tracker is a directory with up to four files:

```
~/personal_db/trackers/<name>/
├── manifest.yaml      # required — schema, schedule, setup steps
├── schema.sql         # required — CREATE TABLE statements
├── ingest.py          # required — sync() and backfill() functions
└── visualizations.py  # optional — dashboard charts
```

### `manifest.yaml`

The source of truth for tracker metadata. Pydantic-validated by `personal_db.manifest.Manifest`. A minimal manifest:

```yaml
name: my_tracker
description: One-line description of what this tracker captures
permission_type: none           # none | api_key | oauth | full_disk_access | manual

setup_steps: []                 # see below

schedule:
  every: 1h                     # or `cron: "0 */6 * * *"` for explicit cron

time_column: ts                 # which column holds the event timestamp
granularity: event              # event | minute | hour | day

schema:
  tables:
    my_tracker:                 # one table per name; multiple tables allowed
      columns:
        id: {type: TEXT,    semantic: "primary key"}
        ts: {type: TEXT,    semantic: "ISO-8601 event time (UTC)"}
        # … your columns …

related_entities: []            # ["people", "topics"] if your tracker uses them
local_only: false               # true if data lives in ~/Library/... that won't survive a reinstall
```

`setup_steps` is a list of typed steps the wizard runs in order:

```yaml
setup_steps:
  - type: env_var
    name: MY_API_KEY
    prompt: "API key for example.com"
    secret: true                # hides input in the terminal
    optional: false             # wizard treats blank as failure
  - type: oauth
    provider: example
    client_id_env: EXAMPLE_CLIENT_ID
    client_secret_env: EXAMPLE_CLIENT_SECRET
    auth_url: https://example.com/oauth/auth
    token_url: https://example.com/oauth/token
    scopes: ["offline", "read:data"]
    redirect_port: 9876
  - type: fda_check
    probe_path: "~/Library/Application Support/SomeApp/data.sqlite"
  - type: instructions
    text: "Visit https://… and create an API key in your account settings."
  - type: command_test
    command: ["which", "git"]
    expect_returncode: 0
```

### `schema.sql`

Plain SQLite DDL. Runs once when the tracker is installed (`personal-db tracker install <name>` or via the web wizard). Subsequent re-installs are idempotent because every statement should use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`.

```sql
CREATE TABLE IF NOT EXISTS my_tracker (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_my_tracker_ts ON my_tracker(ts);
```

The columns here must match what's declared in `manifest.yaml` — the manifest is metadata, the SQL is the actual table.

### `ingest.py`

Two required functions, both taking a `Tracker` object:

```python
from personal_db.tracker import Tracker

def sync(t: Tracker) -> None:
    """Incremental update from the cursor watermark. Idempotent.
    Called every <schedule.every> by the launchd scheduler."""
    ...

def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    """One-shot historical pull. Idempotent.
    Called manually via `personal-db backfill <name>`."""
    ...
```

The `Tracker` object exposes:

| Attribute / method                       | Purpose                                                       |
|------------------------------------------|---------------------------------------------------------------|
| `t.cfg`                                  | `Config` object — paths, db location                          |
| `t.cfg.db_path`                          | Path to `<root>/db.sqlite`                                    |
| `t.cfg.state_dir`                        | Path to `<root>/state/`                                       |
| `t.cursor.get(default=None)`             | Incremental watermark (string)                                |
| `t.cursor.set(value)`                    | Update watermark after a successful sync batch                |
| `t.upsert(table, rows, key)`             | Bulk INSERT … ON CONFLICT(key) DO UPDATE                      |
| `t.log.info(...)`                        | Standard Python logger, namespaced per tracker                |
| `t.resolve_person(alias)`                | Look up or create a person entity (returns id)                |
| `t.resolve_topic(alias)`                 | Look up or create a topic entity                              |

The `cursor` is just a string — interpret it however makes sense for your source. Common choices: an ISO-8601 timestamp (event-time sources), a file mtime (file-system sources), a server-side opaque token (paginated APIs).

### `visualizations.py` (optional)

If present, must export `list_visualizations()` returning a list of dicts:

```python
def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "my_tracker:recent",
            "name": "Recent events",
            "description": "Last 50 entries.",
            "render": _render_recent,    # callable(cfg) -> HTML string
        },
    ]
```

Trackers without a `visualizations.py` get a default `<name>:recent` viz auto-synthesized from the manifest's `time_column` + primary table.

## Worked example: a `downloads` tracker

Say you want to track every file that lands in `~/Downloads` — useful for "what did I download last Tuesday?" agent queries. Here's all four files:

**`manifest.yaml`**
```yaml
name: downloads
description: Files added to ~/Downloads (one row per file, by mtime)
permission_type: none

setup_steps:
  - type: instructions
    text: |
      Tracks files in ~/Downloads. No setup beyond install — just runs.

schedule:
  every: 1h

time_column: added_at
granularity: event

schema:
  tables:
    downloads:
      columns:
        path:       {type: TEXT,    semantic: "absolute file path (primary key)"}
        filename:   {type: TEXT,    semantic: "basename"}
        size_bytes: {type: INTEGER, semantic: "file size in bytes"}
        added_at:   {type: TEXT,    semantic: "ISO-8601 file mtime (UTC)"}

related_entities: []
local_only: true
```

**`schema.sql`**
```sql
CREATE TABLE IF NOT EXISTS downloads (
  path       TEXT PRIMARY KEY,
  filename   TEXT NOT NULL,
  size_bytes INTEGER,
  added_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_downloads_added_at ON downloads(added_at);
```

**`ingest.py`**
```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from personal_db.tracker import Tracker

DOWNLOADS = Path("~/Downloads").expanduser()


def _scan(since_iso: str | None) -> list[dict]:
    """Return rows for files whose mtime is newer than since_iso (or all if None)."""
    if not DOWNLOADS.exists():
        return []
    cutoff = datetime.fromisoformat(since_iso).timestamp() if since_iso else 0
    rows: list[dict] = []
    for entry in DOWNLOADS.iterdir():
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_mtime <= cutoff:
            continue
        rows.append(
            {
                "path": str(entry.resolve()),
                "filename": entry.name,
                "size_bytes": stat.st_size,
                "added_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )
    return rows


def sync(t: Tracker) -> None:
    cursor = t.cursor.get()
    rows = _scan(cursor)
    if not rows:
        t.log.info("no new files")
        return
    n = t.upsert("downloads", rows, key=["path"])
    latest = max(r["added_at"] for r in rows)
    t.cursor.set(latest)
    t.log.info(f"upserted {n} rows; cursor → {latest}")


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    """Re-scan everything regardless of cursor. start/end ignored — Downloads
    is small enough that there's no point chunking."""
    rows = _scan(since_iso=None)
    if not rows:
        return
    t.upsert("downloads", rows, key=["path"])
    latest = max(r["added_at"] for r in rows)
    t.cursor.set(latest)
```

**`visualizations.py`** (optional — skip and the framework auto-synthesizes a `downloads:recent` viz)
```python
import sqlite3

from personal_db.config import Config


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "downloads:by_extension",
            "name": "Top file types (last 30 days)",
            "description": "How many files of each extension you've downloaded recently.",
            "render": _render_by_extension,
        },
    ]


def _render_by_extension(cfg: Config) -> str:
    con = sqlite3.connect(cfg.db_path)
    rows = con.execute(
        """
        SELECT
          LOWER(SUBSTR(filename, INSTR(filename, '.') + 1)) AS ext,
          COUNT(*) AS n
        FROM downloads
        WHERE added_at >= datetime('now', '-30 days')
        GROUP BY ext
        ORDER BY n DESC
        LIMIT 10
        """
    ).fetchall()
    con.close()
    if not rows:
        return '<p class="meta">No downloads in the last 30 days.</p>'
    body = "".join(f"<tr><td><code>.{ext}</code></td><td>{n}</td></tr>" for ext, n in rows)
    return f'<table class="recent-rows"><thead><tr><th>ext</th><th>count</th></tr></thead><tbody>{body}</tbody></table>'
```

## Installing your tracker

Custom tracker (lives only on your machine):

```bash
# Files are at ~/personal_db/trackers/downloads/ — `tracker new` already put them there
personal-db tracker setup downloads     # runs setup_steps, applies schema.sql, runs a test sync
personal-db backfill downloads          # one-time historical pull
```

Bundled tracker (you want to contribute it upstream):

```bash
# Files are at src/personal_db/templates/trackers/downloads/ in a checkout of personal-db
personal-db tracker install downloads   # copies bundle into ~/personal_db/trackers/
personal-db tracker setup downloads
```

The setup wizard menu also detects bundled-but-not-installed trackers automatically and offers to install them.

## Testing

Each bundled tracker has a test in `tests/integration/test_connector_<name>.py`. The conventional pattern:

1. Initialize a temp data root with `subprocess.run([..., "init"])`.
2. Install the tracker with `subprocess.run([..., "tracker", "install", name])`.
3. Monkeypatch the source (e.g. `requests.get`, file system, sqlite path) to return fixture data.
4. Call `sync_one(cfg, name)` from `personal_db.sync`.
5. Read rows out of `<root>/db.sqlite` and assert.

Look at `tests/integration/test_connector_github.py` (HTTP-mocked), `test_connector_chrome_history.py` (SQLite fixture), or `test_connector_habits.py` (manual-only) for working templates.

## Field reference

`manifest.yaml` is validated by `src/personal_db/manifest.py:Manifest`. The full schema is short — read the source. The most-asked-about fields:

- **`time_column`** — which column carries the event time. The framework uses it to compute the `data_horizon` (earliest available date) and to power the auto-synthesized `:recent` viz.
- **`granularity`** — `event` / `minute` / `hour` / `day`. Used by derived trackers and dashboards to bucket data sensibly.
- **`local_only`** — set `true` if your tracker reads from local files that won't survive a system reinstall (`~/Library/...`, app DBs, etc). The framework records each sync's earliest-seen date so derived trackers can flag pre-horizon days as "no data" instead of misattributing them.
- **`related_entities`** — names of entity files under `<root>/entities/`. If your tracker resolves people by name (`t.resolve_person("Alice")`), list `people` here.
- **`schedule.every`** — short forms: `"30s"`, `"5m"`, `"1h"`, `"6h"`, `"24h"`. Or use `cron:` for arbitrary expressions. `null` means manual-only (no auto-sync).

## See also

- `src/personal_db/manifest.py` — the Pydantic schema for `manifest.yaml`.
- `src/personal_db/tracker.py` — the `Tracker` class your `ingest.py` receives.
- `src/personal_db/sync.py` — how `sync()` and `backfill()` are invoked.
- `src/personal_db/mcp_server/prompts/create_tracker.md` — the source of the `create_tracker` Claude prompt.
- Any bundled tracker under `src/personal_db/templates/trackers/` — real working code.
