# personal_db app system design

**Status:** Draft, v0 spine implemented
**Branch:** `codex/app-system-specs`
**Scope:** Introduce a first-class app layer above trackers and marts, so personal_db can support richer local web apps without turning trackers into UI products.

## Summary

Trackers should remain dependable data pipes. Marts should own canonical derived meaning. Apps should own interactive workflows, page layout, visualization, review queues, and user-facing controls.

The app layer is a local, server-rendered web runtime inside the existing daemon. It should use mature browser widgets where they clearly help, while keeping personal_db in charge of routing, data access, permissions, and lifecycle.

For v0, "mart" is a conceptual boundary rather than a separate installed subsystem. The finance tracker currently owns mart-like derived tables such as `finance_transactions`, `finance_accounts`, and `finance_daily_cashflow`. Apps should treat those tables as canonical derived data and avoid taking over cross-source normalization or reconciliation.

## Design Principles

- **personal_db owns the runtime.** Apps run inside the existing FastAPI daemon instead of starting independent Streamlit, Dash, Panel, NiceGUI, or Electron runtimes.
- **Mature libraries provide widgets.** Prefer pinned, vendored browser libraries such as AG Grid, AG Charts, and HTMX-style interactions over hand-rolled grids and charts.
- **The composition surface is ours.** App authors and agents compose approved Python helpers or templates instead of wiring arbitrary frontend frameworks.
- **Data contracts stay explicit.** Apps declare the tables, views, and actions they read or write.
- **Agent edits are reviewable.** Apps live as normal files with manifests, queries, views, tests, and app-specific instructions.
- **Native shell later.** A Tauri/Electron wrapper can be added after the local web app runtime proves itself; it should not be the v0 dependency.

## Proposed Layout

```text
personal_db/
  trackers/
    plaid/
    monarch/
  marts/
    finance/
  apps/
    finance/
      app.yaml
      queries.sql
      views.py
      actions.py
      instructions.md
      tests/
```

For bundled apps, mirror the tracker template pattern:

```text
src/personal_db/templates/apps/<name>/
  app.yaml
  schema.sql
  queries.sql
  views.py
  actions.py
  instructions.md
```

## App Manifest

`app.yaml` declares routing, permissions, and dependencies.

```yaml
name: finance
title: Finance
description: Personal and parent finance dashboard.

reads:
  tables:
    - finance_accounts
    - finance_transactions
    - finance_holdings
    - finance_daily_cashflow
    - finance_daily_net_worth

writes:
  tables:
    - app_finance_transaction_categories
    - app_finance_category_presets
  actions:
    - set_transaction_category
    - clear_transaction_category

pages:
  - slug: overview
    title: Overview
    view: render_overview
  - slug: review
    title: Categorize
    view: render_review
```

The manifest is not a security sandbox by itself. It is a validation and agent-guidance contract enforced by app loaders, review checks, and tests.

## Runtime Model

- `GET /a` lists installed apps.
- `GET /a/<app>` renders the app's default page.
- `GET /a/<app>/<page>` renders a named page.
- `POST /api/apps/<app>/actions/<action>` executes app-owned actions.
- App modules receive a constrained context object:

```python
class AppContext:
    cfg: Config
    app_dir: Path
    query(name: str, **params) -> list[dict]
    action_url(name: str) -> str
```

Queries should prefer named SQL in `queries.sql` so app data access remains easy to inspect.

### v0 discovery decision

Apps are discovered from two locations:

1. bundled templates in `src/personal_db/templates/apps/<name>/`;
2. installed/custom apps in `<root>/apps/<name>/`.

If both exist, the installed/custom app wins. This keeps v0 immediately useful while preserving the same customization model as tracker templates. The CLI exposes `personal-db app available`, `list`, `install`, and `reinstall`.

### v0 query decision

Named SQL uses `-- name: query_name` blocks in `queries.sql`. The v0 loader only accepts `SELECT` and `WITH` statements and returns rows as dictionaries. This is not a complete SQL sandbox, but it establishes a clear read-only app data convention and gives tests/review tooling a concrete surface to inspect.

### v0 schema and action decisions

If an app ships `schema.sql`, the runtime applies it before rendering app pages and before executing app actions. App schemas are for app-owned workflow and presentation state, not source tracker facts.

App actions must be declared in `app.yaml`. Browser-originated action posts are accepted only from the daemon's own origin; cross-origin `Origin`/`Referer` writes are rejected. Local scripts that omit browser provenance headers are still allowed.

App render failures should surface as HTTP 500s. During development this makes broken app pages obvious to tests and monitors instead of silently rendering a successful page with an error paragraph.

## UI Component Layer

Add `personal_db.ui.components` with stable helpers:

- `page`
- `section`
- `metric_card` / `metric_grid`
- `data_grid`
- `chart`
- `tabs`
- `action_button`
- `empty_state`
- `notice`

Important rendering convention: app route templates are only the outer
container. They should not render the app title or page tabs. Each app view
should return exactly one `components.page(...)` call, with `nav=` when the app
has multiple pages, so title/tab chrome is not duplicated.

Data grid convention: app views may pass dictionary rows with either full AG
Grid column definitions or simple string field names. The shared
`components.data_grid(...)` helper owns normalization from string field names
to `{field, headerName}` so apps do not accidentally render blank AG Grid
columns.

Under the hood, these can emit server-rendered HTML plus small data attributes consumed by vendored JavaScript.

Near-term component dependencies:

- AG Grid Community for large tables.
- AG Charts Community for charts.
- A tiny HTMX-style helper or vendored HTMX for partial page refresh and form/action interactions.
- Existing CSS design tokens for spacing, typography, borders, and table density.

## Agent Editing Contract

Each app should ship `instructions.md` containing:

- the app's user intent;
- data contracts it must preserve;
- visual constraints;
- permitted tables/views/actions;
- common edits an agent can safely perform;
- validation commands.

Example agent flow:

1. User opens `/a/finance`.
2. User asks Codex or Claude Code to "make parent draws easier to review."
3. Agent reads `apps/finance/instructions.md`, `app.yaml`, `queries.sql`, and `views.py`.
4. Agent edits the app files.
5. Agent runs app tests and screenshot/render checks.

## v0 Finance Migration

The first app should be `finance`, because it already shows why tracker visualizations are becoming too cramped.

Keep `/t/finance` available during migration. Add `/a/finance` as the richer app surface:

- overview: self vs parents two-column summary;
- self: cashflow, accounts, investments, holdings allocation;
- parents: cashflow, accounts, investments;
- review: transaction categorization with app-owned local overrides;
- settings: app-level display preferences only, not source account export ownership.

The v0 finance app should reuse the existing finance mart tables and mature AG Grid / AG Charts widgets, but page composition should move into `views.py` with named SQL in `queries.sql`. The tracker's `visualizations.py` can remain as a compatibility shim until the app surface is stable.

Finance transaction category overrides are app-owned in v0. Source categories remain visible as read-only provenance. Custom categories become future app presets when saved. If these categories later become canonical inputs to budgets, rollups, search, or other apps, promote them into a first-class finance mart model.

## Non-Goals

- Replacing trackers.
- Replacing the daemon with another framework runtime.
- Adding Electron/Tauri in v0.
- Cross-source financial reconciliation rules beyond the existing finance mart.
- A free-form app builder where arbitrary frontend code bypasses personal_db conventions.

## Open Questions

- When should `marts/` become a first-class installed object rather than a conceptual boundary implemented by tracker-derived tables?
- What is the minimal screenshot/render test harness for local apps?

## Implementation Sketch

1. Add app manifest parser and discovery. **Done.**
2. Add `/a` and `/a/<app>/<page>` routes. **Done.**
3. Add `personal_db.ui.components`. **Done.**
4. Add named SQL loader for `queries.sql`. **Done.**
5. Add bundled `finance` app using existing finance mart tables. **Done.**
6. Keep tracker visualization routes unchanged. **Preserved.**
7. Add focused unit tests for app discovery, manifest validation, route rendering, named query execution, schema application, and actions. **Done.**

## Implementation Plan

### Phase 1: Runtime Spine

- Add `Config.apps_dir`.
- Add `personal_db.apps` with dataclasses for `AppManifest`, `AppPage`, `AppDefinition`, and `AppContext`.
- Discover bundled apps and installed/custom apps, with installed/custom definitions overriding bundled definitions.
- Parse read-only named SQL blocks from `queries.sql`.
- Render app pages through the existing FastAPI/Jinja daemon.
- Apply app schemas centrally before app page renders and app actions.
- Surface app render failures as HTTP 500s.

### Phase 2: Finance App

- Add bundled `finance` app files under `src/personal_db/templates/apps/finance/`.
- Move rich page layout from tracker visualization style into app-owned pages:
  - `overview`
  - `self`
  - `parents`
  - `review`
  - `settings`
- Keep `/t/finance` unchanged while `/a/finance` grows into the primary workspace.

### Phase 3: App Installation And Actions

- Add `personal-db app available/list/install/reinstall` for bundled and installed apps. **Done.**
- Add optional app-owned `schema.sql` for review/workflow state. **Done.**
- Add app action helpers after the first review workflow has a concrete write operation. **Done.**
- Keep app actions separate from tracker actions, declared in the app manifest and guarded by same-origin checks. **Done.**

### Phase 4: Render QA

- Add a minimal screenshot/render check for local app pages.
- Keep unit tests fast by covering discovery, manifest validation, named SQL parsing/execution, and route-level rendering with synthetic databases.
