# personal_db app system design

**Status:** Draft
**Branch:** `codex/app-system-specs`
**Scope:** Introduce a first-class app layer above trackers and marts, so personal_db can support richer local web apps without turning trackers into UI products.

## Summary

Trackers should remain dependable data pipes. Marts should own canonical derived meaning. Apps should own interactive workflows, page layout, visualization, review queues, and user-facing controls.

The app layer is a local, server-rendered web runtime inside the existing daemon. It should use mature browser widgets where they clearly help, while keeping personal_db in charge of routing, data access, permissions, and lifecycle.

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
  actions:
    - sync_finance
    - annotate_transaction

pages:
  - slug: overview
    title: Overview
    view: render_overview
  - slug: accounts
    title: Accounts
    view: render_accounts
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

## UI Component Layer

Add `personal_db.ui.components` with stable helpers:

- `Page`
- `Section`
- `MetricCard`
- `DataGrid`
- `Chart`
- `Tabs`
- `Form`
- `ActionButton`
- `EmptyState`
- `Notice`

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
- review: parent draw transactions and recurring/subscription candidates;
- settings: app-level display preferences only, not source account export ownership.

## Non-Goals

- Replacing trackers.
- Replacing the daemon with another framework runtime.
- Adding Electron/Tauri in v0.
- Cross-source financial reconciliation rules beyond the existing finance mart.
- A free-form app builder where arbitrary frontend code bypasses personal_db conventions.

## Open Questions

- Should apps be installed into `<root>/apps/<name>` like trackers, or can bundled apps run directly from package templates until edited?
- Should `marts/` become a first-class installed object, or remain tracker-shaped for now?
- Do app actions share the tracker action endpoint implementation, or get a separate loader for clearer boundaries?
- Should app-level SQL be limited to read-only statements by default?
- What is the minimal screenshot/render test harness for local apps?

## Implementation Sketch

1. Add app manifest parser and discovery.
2. Add `/a` and `/a/<app>/<page>` routes.
3. Add `personal_db.ui.components`.
4. Add named SQL loader for `queries.sql`.
5. Add bundled `finance` app using existing finance mart tables.
6. Keep tracker visualization routes unchanged.
7. Add focused unit tests for app discovery, manifest validation, route rendering, and named query execution.
