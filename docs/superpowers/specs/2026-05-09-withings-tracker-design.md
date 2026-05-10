# Withings tracker — design

Status: approved (pending implementation plan)
Date: 2026-05-09

## Goal

Add a bundled tracker that pulls body-composition measurements from a Withings smart scale (weight, body fat %, fat mass, lean/muscle/bone mass, hydration, heart pulse) into `personal_db`. Modeled on the existing `whoop` tracker for OAuth shape, with a small framework extension to handle Withings' non-standard token endpoint.

Reference docs: <https://developer.withings.com/api-reference> (OAuth2 + `Measure v2 - Getmeas`).

## Non-goals

- Withings activity, sleep, blood pressure, ECG, or temperature data. The user does not own those products. Tracker is scoped to scale data only; future trackers can add new tables for other devices.
- Generalizing the OAuth helpers with provider-specific knobs. The whole point of the adapter pattern below is that `oauth.py` stays clean of per-provider quirks.
- Computing BMI. The Measure API doesn't return it (height isn't in this endpoint). If wanted, derive at query time from weight and a stored height constant — out of scope here.
- Backfill bound by date. Withings' history is small (one weigh-in per day at most), so an unbounded initial pull is fine. `backfill(start, end)` will accept the args but the simplest impl just calls `sync(t)`.

## File layout

Standard 4-file bundled tracker, plus a fifth Withings-specific file for the OAuth adapter:

```
src/personal_db/templates/trackers/withings/
  __init__.py
  manifest.yaml
  schema.sql
  ingest.py
  oauth_adapter.py        # NEW: WithingsAdapter implementing TokenAdapter
  visualizations.py
```

The framework change touches three existing files (see "Framework changes" below). Everything else lives inside the tracker dir.

## Framework changes

The whole reason for these is so future non-standard OAuth providers can each ship their own adapter without touching `oauth.py`.

### 1. `src/personal_db/oauth.py` — adapter mechanism

Introduce a `TokenAdapter` Protocol, a registry, and route `refresh_if_needed` / `exchange_code` through it.

```python
from typing import Protocol, Any

class TokenAdapter(Protocol):
    def exchange_code(
        self, *, token_url: str, client_id: str, client_secret: str,
        code: str, redirect_uri: str,
    ) -> dict[str, Any]: ...

    def refresh_token(
        self, *, token_url: str, client_id: str, client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]: ...

    # Both must return a token dict containing at least:
    #   access_token, refresh_token, expires_in
    # The dispatch layer adds expires_at = time.time() + expires_in.

class StandardAdapter:
    """Default RFC-6749-shaped flow. Used when no adapter is registered."""
    # Implements current behavior of exchange_code / refresh_if_needed.

_adapters: dict[str, TokenAdapter] = {}

def register_adapter(provider: str, adapter: TokenAdapter) -> None:
    _adapters[provider] = adapter

def _adapter_for(provider: str) -> TokenAdapter:
    return _adapters.get(provider, StandardAdapter())
```

The existing `refresh_if_needed(...)` and `exchange_code(...)` keep their public signatures — they become thin wrappers that look up `_adapter_for(provider)` and delegate. Result: whoop, oura, granola call sites are unchanged; their token flows still go through `StandardAdapter`.

`exchange_code` currently doesn't take `provider` as a parameter. Add it (default `"_standard"`) and update the one call site in `start_web_oauth` to pass it through. Backwards-compatible because the default routes to `StandardAdapter`.

### 2. `src/personal_db/manifest.py` — `adapter` field on `OAuthStep`

```python
class OAuthStep(BaseModel):
    type: Literal["oauth"]
    provider: str
    adapter: str | None = None    # NEW: "<module>:<class>" relative to tracker dir
    client_id_env: str
    client_secret_env: str
    # ...rest unchanged
```

When `adapter` is set, both the setup wizard and the tracker's `sync()` ensure the adapter is registered before any token operation runs. Implementation helper:

```python
# in oauth.py
def ensure_adapter_from_manifest(tracker_dir: Path, step: OAuthStep) -> None:
    """Idempotent: imports `<tracker_dir>/<module>.py`, instantiates <class>,
    and registers it under step.provider. No-op if step.adapter is None or
    the provider is already registered."""
```

### 3. Setup wizard

In `src/personal_db/tracker_setup.py` (or wherever `OAuthStep` is dispatched today — to be confirmed during planning), call `ensure_adapter_from_manifest(...)` before `start_web_oauth(...)`. One line.

The tracker's `ingest.py` calls the same helper at the top of `sync()` so background syncs work too. (Alternatively the base `Tracker` class can do it once on load — pick whichever fits the existing structure during planning. Either way it's idempotent.)

## WithingsAdapter

Withings deviates from RFC 6749 in two specific ways:

1. **Required form param `action=requesttoken`** on every token request (both initial code exchange and refresh).
2. **Response envelope:** success response is `{"status": 0, "body": {access_token, refresh_token, expires_in, ...}}`. Non-zero `status` = error; the body may also be absent. The actual token fields live one level deeper.

`WithingsAdapter` handles both:

```python
# trackers/withings/oauth_adapter.py
import requests

class WithingsAdapter:
    TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"

    def exchange_code(self, *, token_url, client_id, client_secret, code, redirect_uri):
        return self._post({
            "action": "requesttoken",
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        })

    def refresh_token(self, *, token_url, client_id, client_secret, refresh_token):
        return self._post({
            "action": "requesttoken",
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        })

    def _post(self, data: dict) -> dict:
        r = requests.post(self.TOKEN_URL, data=data, timeout=10)
        r.raise_for_status()
        envelope = r.json()
        if envelope.get("status") != 0:
            raise RuntimeError(f"Withings token error: {envelope}")
        body = envelope.get("body") or {}
        # Standard token shape expected by oauth.py: access_token, refresh_token, expires_in
        return {
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_in": int(body.get("expires_in", 10800)),
            "userid": body.get("userid"),
            "scope": body.get("scope"),
            "token_type": body.get("token_type", "Bearer"),
        }
```

Note: the `token_url` param passed in by callers is ignored — we hard-code `TOKEN_URL` since it's the only Withings token endpoint. Manifest still declares it (for documentation and so the standard flow has a value to thread through), but the adapter is the source of truth.

## Manifest

```yaml
name: withings
description: Withings smart-scale body composition (weight, fat %, muscle, bone, etc.)
permission_type: oauth
setup_steps:
  - type: env_var
    name: WITHINGS_CLIENT_ID
    prompt: "Withings OAuth client ID (from developer.withings.com)"
  - type: env_var
    name: WITHINGS_CLIENT_SECRET
    prompt: "Withings OAuth client secret"
    secret: true
  - type: oauth
    provider: withings
    adapter: oauth_adapter:WithingsAdapter
    client_id_env: WITHINGS_CLIENT_ID
    client_secret_env: WITHINGS_CLIENT_SECRET
    auth_url: "https://account.withings.com/oauth2_user/authorize2"
    token_url: "https://wbsapi.withings.net/v2/oauth2"
    scopes: ["user.metrics"]
    redirect_host: localhost
    redirect_port: 9877      # distinct from whoop's 9876
schedule:
  every: 6h
time_column: date
granularity: event
schema:
  tables:
    withings_measurements:
      columns:
        grpid:            {type: TEXT,    semantic: "Withings measure-group id, primary key"}
        date:             {type: TEXT,    semantic: "ISO-8601 measurement time (UTC)"}
        timezone:         {type: TEXT,    semantic: "IANA tz reported by Withings (e.g. America/Los_Angeles); per-row because user travels"}
        attrib:           {type: INTEGER, semantic: "0=device user known, 1=device ambiguous, 2=manual entry, 4=manual ambiguous, 5=user-create, 7=auto, 8=device hash unknown"}
        category:         {type: INTEGER, semantic: "1=real measurement, 2=user objective"}
        device_id:        {type: TEXT,    semantic: "Withings deviceid (raw, may be empty for manual entries)"}
        weight_kg:        {type: REAL,    semantic: "weight in kilograms (Withings type 1)"}
        fat_ratio_pct:    {type: REAL,    semantic: "body fat percentage (type 6)"}
        fat_mass_kg:      {type: REAL,    semantic: "fat mass in kg (type 8)"}
        lean_mass_kg:     {type: REAL,    semantic: "fat-free mass in kg (type 5)"}
        muscle_mass_kg:   {type: REAL,    semantic: "muscle mass in kg (type 76); often equals lean mass on basic scales"}
        bone_mass_kg:     {type: REAL,    semantic: "bone mass in kg (type 88)"}
        hydration_kg:     {type: REAL,    semantic: "body water in kg (type 77)"}
        heart_pulse_bpm:  {type: INTEGER, semantic: "pulse at weigh-in (type 11); only on Body Cardio / Body Comp / Body Smart"}
        created_at:       {type: TEXT,    semantic: "ISO-8601 record creation time (UTC)"}
        modified_at:      {type: TEXT,    semantic: "ISO-8601 last modification time (UTC); used for cursor"}
related_entities: []
```

`time_column: date` matches the canonical "when did the weigh-in happen" column. `granularity: event` because each weigh-in is a discrete event, not an aggregated daily summary.

## Schema

```sql
CREATE TABLE IF NOT EXISTS withings_measurements (
  grpid            TEXT PRIMARY KEY,
  date             TEXT NOT NULL,
  timezone         TEXT,
  attrib           INTEGER,
  category         INTEGER,
  device_id        TEXT,
  weight_kg        REAL,
  fat_ratio_pct    REAL,
  fat_mass_kg      REAL,
  lean_mass_kg     REAL,
  muscle_mass_kg   REAL,
  bone_mass_kg     REAL,
  hydration_kg     REAL,
  heart_pulse_bpm  INTEGER,
  created_at       TEXT,
  modified_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_withings_measurements_date ON withings_measurements(date);
CREATE INDEX IF NOT EXISTS idx_withings_measurements_modified_at ON withings_measurements(modified_at);
```

The `modified_at` index supports the `lastupdate` cursor.

## Sync algorithm

Single endpoint: `POST https://wbsapi.withings.net/measure` with form body:

| Param | Value |
|---|---|
| `action` | `getmeas` |
| `meastypes` | `1,5,6,8,11,76,77,88` (CSV — only request the types we store) |
| `category` | `1` (real measurements; skip user objectives) |
| `lastupdate` | cursor value (Unix seconds) — omit on first run for full backfill |
| `offset` | `0` initially; bumped per Withings' pagination |

Auth: `Authorization: Bearer <access_token>` header. (Withings also accepts `access_token` as a form param — Bearer header is cleaner.)

Response shape:

```json
{
  "status": 0,
  "body": {
    "updatetime": 1746752400,
    "timezone": "America/Los_Angeles",
    "more": 0,
    "offset": 0,
    "measuregrps": [
      {
        "grpid": 12345,
        "attrib": 0,
        "date": 1746752400,
        "created": 1746752400,
        "modified": 1746752400,
        "category": 1,
        "deviceid": "abc",
        "timezone": "America/Los_Angeles",
        "measures": [
          {"value": 80123, "type": 1, "unit": -3},
          {"value": 18234, "type": 6, "unit": -3}
        ]
      }
    ]
  }
}
```

`measures[].value * 10**unit` = float value in the unit's natural scale (kg, %, bpm).

```
sync(t):
  ensure_adapter_from_manifest(tracker_dir, oauth_step)   # idempotent
  cid, cs = client_credentials_from_env()
  token = refresh_if_needed(cfg, "withings", token_url=..., client_id=cid, client_secret=cs)
  cursor = Cursor("withings:measurements", t.cfg.state_dir)

  rows = []
  offset = 0
  while True:
    body = _fetch_measures(
      token,
      meastypes="1,5,6,8,11,76,77,88",
      category=1,
      lastupdate=cursor.get(),       # None on first run
      offset=offset,
    )
    rows.extend(_flatten(g, body["timezone"]) for g in body["measuregrps"])
    if not body.get("more"):
      break
    offset = body["offset"]

  if rows:
    t.upsert("withings_measurements", rows, key=["grpid"])
    cursor.set(str(max(r["_modified_unix"] for r in rows)))   # see flatten below
  t.log.info("withings measurements: %d", len(rows))


backfill(t, start, end):
  sync(t)   # Withings has no separate backfill endpoint; lastupdate cursor handles it.
```

### Cursor

`Cursor("withings:measurements")` — value is the maximum `modified` (Unix seconds) seen, stored as a string. Passed back to the API as `lastupdate=<value>` on the next sync. Withings returns groups whose `modified > lastupdate`, so corrections to historical weigh-ins (rare) are captured.

First run: cursor is `None`, no `lastupdate` param sent, full history is returned in pages.

### Flatten

```python
TYPE_MAP = {
    1:  "weight_kg",
    5:  "lean_mass_kg",
    6:  "fat_ratio_pct",
    8:  "fat_mass_kg",
    11: "heart_pulse_bpm",
    76: "muscle_mass_kg",
    77: "hydration_kg",
    88: "bone_mass_kg",
}

def _flatten(grp: dict, default_tz: str) -> dict:
    row = {
        "grpid": str(grp["grpid"]),
        "date": _iso_utc(grp["date"]),
        "timezone": grp.get("timezone") or default_tz,
        "attrib": grp.get("attrib"),
        "category": grp.get("category"),
        "device_id": grp.get("deviceid"),
        "created_at": _iso_utc(grp.get("created")),
        "modified_at": _iso_utc(grp.get("modified")),
        "_modified_unix": int(grp.get("modified") or grp["date"]),  # for cursor; stripped before upsert
        # All measure columns default to NULL:
        "weight_kg": None, "fat_ratio_pct": None, "fat_mass_kg": None,
        "lean_mass_kg": None, "muscle_mass_kg": None, "bone_mass_kg": None,
        "hydration_kg": None, "heart_pulse_bpm": None,
    }
    for m in grp.get("measures") or []:
        col = TYPE_MAP.get(m["type"])
        if not col:
            continue                       # unknown measure type → ignore
        scaled = m["value"] * (10 ** m["unit"])
        row[col] = int(scaled) if col == "heart_pulse_bpm" else float(scaled)
    return row
```

`_iso_utc(int_seconds)` → ISO-8601 UTC string (e.g. `"2026-05-09T15:42:00+00:00"`). The per-row `timezone` column preserves the local context for analyses split by location (per the existing whoop-timezone memory note: never use SQLite `localtime`; use the stored offset).

`_modified_unix` is internal — popped from each row before `upsert`, used only to compute `max(...)` for the cursor.

## Error handling

| Failure | Behavior |
|---|---|
| `WITHINGS_CLIENT_ID` / `_SECRET` missing | `RuntimeError("Set WITHINGS_CLIENT_ID and WITHINGS_CLIENT_SECRET")` (matches whoop). |
| OAuth token file missing | `RuntimeError("withings: no refresh_token; re-run setup")` (existing oauth.py behavior). |
| Token endpoint returns `status != 0` | `WithingsAdapter` raises `RuntimeError(f"Withings token error: {envelope}")`. Common: status 401 (invalid_token) → user must re-auth. |
| Measure endpoint returns `status != 0` | `RuntimeError(f"Withings measure error: status={status}")`. The Withings API uses 200 OK with non-zero status for application-level errors, so we check status before consuming `body`. |
| Network / 5xx | propagate; sync layer logs and isolates. |
| Unknown measure type in response | silently skipped (TYPE_MAP miss). |
| Manual entry (attrib in {2,4}) | stored, attrib column preserved so caller can filter `WHERE attrib NOT IN (2,4)` for device-only measurements. |

## Visualizations

Two charts at first; cheap to add more later.

- **`weight_trend_180d`** — daily weight in kg over the last 180 days. Line/bar, color-graded relative to user's running median (lighter = below median, darker = above). One row per day; if the user weighs multiple times in a day, take the most recent (`MAX(date)` per day). Excludes manual entries (`attrib NOT IN (2,4)`).
- **`body_composition_30d`** — last 30 days, stacked bars per weigh-in showing `fat_mass_kg` + `lean_mass_kg` (the two account for total body weight on most scales). Quickly visualizes whether weight changes are coming from fat or lean tissue.

Both reuse `personal_db.ui.charts.vertical_bars` (whoop already uses this helper — same pattern).

## Testing

`tests/unit/test_withings_tracker.py`:

1. **`WithingsAdapter.exchange_code` happy path** — mock `requests.post` returning `{"status": 0, "body": {"access_token": "a", "refresh_token": "r", "expires_in": 10800, "userid": 1}}`; assert returned dict has flat `access_token`/`refresh_token`/`expires_in`, and that the request body included `action=requesttoken` and `grant_type=authorization_code`.
2. **`WithingsAdapter.refresh_token` happy path** — analogous, `grant_type=refresh_token`.
3. **`WithingsAdapter` non-zero status** — `{"status": 401, "error": "invalid_token"}` → `RuntimeError` containing the envelope.
4. **Adapter registration via manifest** — `ensure_adapter_from_manifest` on a fixture tracker dir with `adapter: oauth_adapter:WithingsAdapter`; assert `register_adapter("withings", ...)` was called and idempotent on second invocation.
5. **`refresh_if_needed` routes through registered adapter** — register a fake adapter; call `refresh_if_needed`; assert the fake's `refresh_token` was invoked, not `StandardAdapter`'s.
6. **`StandardAdapter` unchanged** — call `refresh_if_needed` for a provider with no registered adapter; assert behavior matches today's tests (existing test should still pass without modification).
7. **`_flatten` full row** — a measuregrp with all 8 measure types; assert every column populated, units scaled correctly (e.g. `value=80123, unit=-3` → `weight_kg=80.123`).
8. **`_flatten` partial row** — measuregrp with only weight; assert all other measure columns are `None` and the row still upserts cleanly.
9. **`_flatten` unknown type** — measure with `type=999`; silently dropped.
10. **`_flatten` timezone fallback** — group with no per-row `timezone` falls back to body-level `timezone`.
11. **Sync pagination** — mock `_fetch_measures` returning two pages (`more=1, offset=200`, then `more=0`); assert both pages' rows are upserted and final cursor = max `modified` across both pages.
12. **Sync first run (no cursor)** — `lastupdate` not sent in request params.
13. **Sync incremental run** — cursor set; assert `lastupdate=<cursor>` is sent.

Plus the existing manifest-loads + tracker-installs smoke tests cover the new `adapter` field and `tracker reinstall`.

## Open implementation notes (not blockers)

- The `redirect_port: 9877` is just an unused port near whoop's. The user must register `http://localhost:9877/callback` as the redirect URI in the Withings developer console. Setup wizard already prints the URL during the OAuth step.
- Withings' `more` flag has been observed to occasionally lie (return `more=1` then an empty next page). The pagination loop handles this: if `measuregrps` is empty on a non-first page, break.
- If the user later adds a Withings watch / sleep mat / BPM, that's a new tracker (different table, possibly different scopes). The OAuth client / refresh token can be reused — Withings issues one refresh token per (client, user) pair regardless of scope. Out of scope for this design.
- Manual entries (`attrib in {2,4}`) are stored for completeness but visualizations exclude them by default. If the user explicitly logs missing weigh-ins by hand, that decision can be revisited.
