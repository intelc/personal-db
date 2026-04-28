# Tracker Transforms — Design

**Date:** 2026-04-28
**Status:** Approved for implementation planning
**Scope:** Add a within-tracker transformation layer to personal_db so a tracker can declare a small DAG of derived tables on top of its raw ingest, including LLM/API enrichments.

---

## Motivation

Today, a tracker has a single `ingest.py` that fetches data and writes it to one or more raw tables defined in `schema.sql`. Anything beyond raw ingest — categorization, summarization, enrichment via external APIs — has to be hand-rolled in `ingest.py` or implemented as a separate "derived tracker" (the `daily_time_accounting` pattern).

That pattern works but doesn't compose: a tracker that wants both raw GPS coordinates *and* a human-readable place name (via LLM lookup) *and* a daily summary table currently has no clean way to express that pipeline. Each derived step would need its own tracker dir, manifest, schedule, and cursor.

The goal is to let a single tracker declare:
1. Its raw table(s), via the existing `ingest.py` + `schema.sql`.
2. Zero or more transforms that read from upstream tables (raw or other transforms) and write to their own tables.
3. The dependency graph between them.

The framework runs them in topological order on each sync, with per-transform cursors for incrementality and a built-in helper for cached LLM/API enrichment.

## Non-goals (v1)

- **Cross-tracker transforms.** A transform reads/writes only within its own tracker. Existing cross-tracker derived trackers (e.g., `daily_time_accounting`) keep working unchanged; they remain hand-rolled.
- **Per-transform schedules.** Transforms inherit their tracker's `schedule.every` cadence. No `transform.geocoded.every: 1h`.
- **Test framework / freshness assertions.** dbt has these; we don't need them yet.
- **Dedicated CLI for running one transform.** Workaround: clear the per-transform cursor and run sync.
- **DAG visualization command.** Easy to add later.
- **Replacing dbt or any external tool.** This is purpose-built for personal_db's "single SQLite file, ship-with-tracker" model.

## Architecture

### File layout

No new files per tracker. Transforms live in `ingest.py` as decorated functions alongside `sync()`.

```
trackers/<name>/
  manifest.yaml      # unchanged
  ingest.py          # sync() PLUS @transform-decorated functions
  schema.sql         # raw tables AND transform target tables, all in one place
  visualizations.py  # unchanged
```

Rationale: matches the project's existing "one file per concern" aesthetic without forcing every tracker to grow a `transforms/` subdir. Most trackers will have zero or one transform; a `transforms/` directory would be empty noise.

### The decorator

Lives in a new module `personal_db.transforms`.

```python
def transform(*, writes: str, depends_on: list[str]):
    """Mark a function in ingest.py as a transform.

    writes:      table this transform populates (must exist in schema.sql)
    depends_on:  tables this transform reads (used for topo-sort)
    """
    def deco(fn):
        fn._transform_spec = TransformSpec(
            name=fn.__name__,
            fn=fn,
            writes=writes,
            depends_on=list(depends_on),
        )
        return fn
    return deco
```

The spec is attached to the function object — no global registry, no double-registration risk under `_load_ingest_module`'s hot-reload (`sync.py:33`). Discovery walks `vars(mod).values()` looking for the `_transform_spec` attribute.

### The context object

Built fresh for each transform invocation.

```python
@dataclass
class TransformContext:
    con: sqlite3.Connection      # row_factory=Row, used for SQL transforms
    cursor: TransformCursor      # per-transform, namespaced "<tracker>:<transform_name>"
    log: logging.Logger
    enrich: Callable             # bound helper, see "The enrich helper" section
```

`TransformCursor` reuses the existing `Cursor` class in `tracker.py:13`, instantiated with the namespaced name so each transform has its own state in `cursors.sqlite`.

### Sync flow

`sync.py:sync_one` changes from:

```python
mod = _load_ingest_module(...)
mod.sync(t)
```

to:

```python
mod = _load_ingest_module(...)
mod.sync(t)

specs = [v._transform_spec for v in vars(mod).values()
         if hasattr(v, "_transform_spec")]
_validate(specs, schema_tables)              # see "Validation"

failed: set[str] = set()
for spec in topo_sort(specs):
    if any(dep in failed for dep in spec.depends_on):
        # upstream transform failed this tick — skip downstream
        continue
    ctx = make_ctx(t, spec)
    try:
        spec.fn(t, ctx)
    except Exception as e:
        failed.add(spec.writes)
        _record_error(t.cfg, name, spec.name, e)
        # continue to next transform
```

Independent branches still run when one fails. Errors land in the existing `state/sync_errors.jsonl`.

### Failure semantics

The framework opens a transaction on `ctx.con` around `spec.fn(t, ctx)`. Anything the user writes via `ctx.con` (i.e. plain-SQL transforms) benefits from that outer transaction — failure rolls the whole thing back, cursor doesn't advance.

`enrich` is different: internally it opens its **own** SQLite connection (separate from `ctx.con`) and runs each batch as a self-contained transaction on that connection. This is the cleanest way to give it independent commit cadence — SQLite has no clean "ignore the outer transaction" primitive, so a separate connection is the right tool.

Consequence:
- Pure SQL transform fails → all its writes roll back (atomic).
- `enrich` transform fails mid-batch → up to `batch_size` rows of progress are lost (cache writes for that failed batch roll back too, so they get recomputed cleanly next sync). Already-committed batches persist.
- Mixed transform (writes to `ctx.con` AND calls `ctx.enrich`) → the SQL writes are bound to the outer transaction; enrich batches commit independently. If the user is doing this they should know what they're getting; we don't expect it to be common.

### Backfill

`mod.backfill(t, start, end)` runs as today, then transforms run after — they pick up new raw rows via their cursor naturally. To force a transform to re-process from scratch, the user clears its cursor:

```bash
sqlite3 ~/personal_db/state/cursors.sqlite \
  "DELETE FROM cursors WHERE name='location:geocoded'"
```

## The `enrich` helper

The workhorse for LLM/API enrichments. A method on `TransformContext`.

```python
def enrich(
    self,
    *,
    source: str,                                         # table to read from
    target: str,                                         # table to write to
    fn: Callable[[sqlite3.Row], dict],                   # row → enrichment cols (NOT including key)
    source_key: str | None = None,                       # FK column copied to target; defaults to source PK
    dedup_key: Callable[[sqlite3.Row], str] | None = None,  # optional content-addressed cache key
    batch_size: int = 1,                                 # commit every N rows
    where: str | None = None,                            # optional extra WHERE clause
) -> int:                                                # returns count of source rows processed
```

**`source_key` default.** If not specified, the framework runs `PRAGMA table_info(source)` and uses the column with `pk=1`. If the source has a composite primary key or no primary key, the framework raises a hard error — the user must specify `source_key` explicitly.

**What the framework does:**

1. Read `ctx.cursor` to find the last processed `source_key` value.
2. Fetch new rows: `SELECT * FROM source WHERE source_key > ? [AND where] ORDER BY source_key`.
3. For each row, in batches of `batch_size`:
   - If `dedup_key` is set, compute the key. Look it up in `_<target>_cache`. Cache hit → reuse stored result. Cache miss → call `fn(row)`, store result in cache.
   - If `dedup_key` is not set, always call `fn(row)`.
   - Upsert `{source_key: row[source_key], **result}` into `target`.
   - Advance `ctx.cursor` to this row's `source_key`.
4. Each batch commits as one transaction (cache write + target write + cursor advance, atomic together).

**The dedup cache.** Auto-created lazily as:

```sql
CREATE TABLE IF NOT EXISTS _<target>_cache (
  key TEXT PRIMARY KEY,
  value TEXT  -- JSON-encoded fn output
);
```

Underscore-prefixed so it doesn't pollute the user's mental model of "their" tables. Never declared in `schema.sql`.

**What `fn` is responsible for:** the API call and shaping the return dict. Nothing else.

**What `fn` is NOT responsible for:** cache management, batching, transactions, advancing cursors, dedup logic, retries.

## Validation

Run at sync time, after `_load_ingest_module` and before executing any transform. All four are hard errors that abort the tracker run:

1. Every `writes` target must exist as a `CREATE TABLE` in `schema.sql`.
2. Every entry in `depends_on` must either exist in `schema.sql` or be the `writes` target of another transform in the same tracker.
3. No two transforms may share the same `writes` target.
4. The DAG of `(transform.writes ← transform.depends_on)` edges must be acyclic. Cycle detection should report the cycle path.

Errors land in `state/sync_errors.jsonl` like any other tracker failure.

## Worked example: a `location` tracker

`schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS raw_locations (
  id INTEGER PRIMARY KEY,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geocoded_locations (
  source_id INTEGER PRIMARY KEY REFERENCES raw_locations(id),
  place_name TEXT
);

CREATE TABLE IF NOT EXISTS daily_locations (
  date TEXT NOT NULL,
  place_name TEXT NOT NULL,
  visits INTEGER NOT NULL,
  PRIMARY KEY (date, place_name)
);
```

`ingest.py`:

```python
from datetime import date, timedelta

import anthropic

from personal_db.tracker import Tracker
from personal_db.transforms import transform

_client = anthropic.Anthropic()


def sync(t: Tracker) -> None:
    """Pull GPS points from wherever, write to raw_locations."""
    rows = _fetch_from_phone_export(since=t.cursor.get())
    t.upsert("raw_locations", rows, key=["id"])
    if rows:
        t.cursor.set(rows[-1]["ts"])


def _lookup_place(row) -> dict:
    msg = _client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=80,
        messages=[{
            "role": "user",
            "content": f"Place name for lat={row['lat']}, lon={row['lon']}. One short phrase.",
        }],
    )
    return {"place_name": msg.content[0].text.strip()}


@transform(writes="geocoded_locations", depends_on=["raw_locations"])
def geocoded(t: Tracker, ctx) -> None:
    ctx.enrich(
        source="raw_locations",
        target="geocoded_locations",
        fn=_lookup_place,
        dedup_key=lambda r: f"{round(r['lat'], 4)},{round(r['lon'], 4)}",
        batch_size=10,
    )


@transform(writes="daily_locations", depends_on=["geocoded_locations"])
def daily_summary(t: Tracker, ctx) -> None:
    """Pure SQL: rebuild last 7 days of summary."""
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    ctx.con.execute("DELETE FROM daily_locations WHERE date >= ?", (cutoff,))
    ctx.con.execute("""
        INSERT INTO daily_locations (date, place_name, visits)
        SELECT date(r.ts), g.place_name, count(*)
        FROM raw_locations r
        JOIN geocoded_locations g ON g.source_id = r.id
        WHERE date(r.ts) >= ?
        GROUP BY date(r.ts), g.place_name
    """, (cutoff,))
```

**On every `sync_one("location")` call:**

1. `sync(t)` runs, writes new GPS points to `raw_locations`.
2. Framework discovers two transforms via `_transform_spec`.
3. Topo-sorts: `geocoded` (deps `raw_locations`) → `daily_summary` (deps `geocoded_locations`).
4. `geocoded` runs: hits the LLM only for new unique rounded coords (~200 unique places across ~10k GPS points), writes to `geocoded_locations` with cache hits filling in the rest.
5. `daily_summary` runs: pure SQL rebuild of the last 7 days.

If the LLM call fails partway through `geocoded`, `daily_summary` is skipped this tick (its dep failed). On the next sync, `geocoded` resumes from where its cursor stopped.

## Files touched / created

**New:**
- `src/personal_db/transforms.py` — `@transform` decorator, `TransformSpec`, `TransformContext`, `enrich` helper, topo sort, validation.
- `tests/unit/test_transforms.py` — decorator behavior, topo sort, dedup cache, failure handling.

**Modified:**
- `src/personal_db/sync.py` — discover transforms after `mod.sync(t)`, validate, run in topo order, handle per-transform failures.
- `src/personal_db/tracker.py` — possibly expose `TransformCursor` (could just live in `transforms.py`).

**Unchanged:**
- `manifest.py` — no new manifest fields. Transforms are pure-Python in `ingest.py`.
- All existing trackers — including `daily_time_accounting`, which keeps its hand-rolled cross-tracker shape.
- `installer.py` / `tracker reinstall` — already overwrites `ingest.py` and `schema.sql` (the only canonical files transforms touch), so no change needed.

## Open questions deferred to implementation

- Exact shape of `TransformContext.con` — fresh connection per transform, or reuse a single connection across all transforms in a sync run? (Leaning fresh per transform for isolation, but worth measuring.)
- Logging granularity: per-transform-start, per-batch, per-row? (Default to per-transform start/end + per-error row.)
- Whether the source-row WHERE clause should use `>` (strict) or `>=` semantics on the cursor. `>` is correct when `source_key` is a unique INTEGER PK; for non-unique keys it could skip rows. Initial implementation can require `source_key` to be UNIQUE and validate it with `PRAGMA index_list`.

These are tactical and don't affect the public API.
