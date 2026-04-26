---
name: personal-db-insights
description: Generate analysis of personal_db tracker data and write it to notes/. Use when the user asks for insights, patterns, weekly review, correlations, or comparisons across tracked metrics.
---

# Personal DB — Insights

You have access to the user's personal_db via MCP tools. Use them to answer questions about their data and write a markdown analysis.

## Available MCP tools (server `personal_db`)

- `list_trackers()` — see what's tracked
- `describe_tracker(name)` — get the schema/manifest before querying
- `query(sql)` — read-only SQL against `db.sqlite`
- `get_series(tracker, range, granularity?, agg?, value_column?)` — bucketed time series
- `list_entities(kind, query?)` — people/topics
- `log_event(tracker, fields)` — only when explicitly asked to log
- `list_notes(query?) / read_note(path)` — prior analyses

## Workflow for `/insights <question>`

1. Call `list_trackers` to see what data is available.
2. For each tracker that might be relevant, call `describe_tracker` to learn the schema. Don't guess columns.
3. Write `query` or `get_series` calls to fetch the data you need. Prefer `get_series` for time-bucketed comparisons.
4. Reason from the data. Note ambiguity. State sample sizes.
5. Use `log_event` only if the user asked to log something.
6. Write the analysis as a markdown file under the personal_db root's `notes/` directory using the `Write` tool. Filename convention: `notes/<YYYY-MM-DD>-<short-slug>.md`. The MCP `list_notes` tool auto-indexes any `.md` files it finds in `notes/`, so no extra registration step is needed.

## Output format for the note

````markdown
# <Topic> — <Date>

**Question:** <restate user question>

**Data sources:** <trackers used + date range>

## Findings

- <bullet>
- <bullet>

## Caveats

- <small sample size? confounders? data quality issues?>

## Charts (optional)

(ASCII sparklines or markdown tables only — no SVG/HTML in v0.)
````

## Style rules

- Be honest about uncertainty. Three weeks of data is not enough for "you sleep worse on Tuesdays."
- Show the SQL or `get_series` call you used. The user is technical and will want to verify.
- Never fabricate numbers. If the data isn't there, say so.
