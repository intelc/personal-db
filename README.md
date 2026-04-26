# personal_db

Personal data layer for AI agents. SQLite + per-tracker scripts + MCP server. macOS only in v0.

## Install

```bash
git clone <this repo>
cd personal_db
./scripts/install_dev.sh
source .venv/bin/activate
```

## Quick start

```bash
# Initialize the data root (default ~/personal_db)
personal-db init

# Install some built-in trackers
personal-db tracker install github_commits
personal-db tracker install whoop
personal-db tracker install screen_time
personal-db tracker install imessage
personal-db tracker install habits

# For each, run setup
export GITHUB_TOKEN=…  GITHUB_USER=…
export WHOOP_CLIENT_ID=…  WHOOP_CLIENT_SECRET=…
personal-db permission check screen_time   # opens System Settings if FDA missing
personal-db permission check imessage      # same

# First run: backfill what's available
personal-db backfill github_commits
personal-db backfill whoop

# Install the launchd scheduler (runs `personal-db sync --due` every 10 min)
personal-db scheduler install

# Add the MCP server to Claude Code
claude mcp add personal_db -- personal-db mcp

# Install the /insights skill
mkdir -p ~/.claude/skills/personal-db
cp src/personal_db/templates/claude_skill/insights.md ~/.claude/skills/personal-db/
```

## CLI argument order note

`--root` is a *global* option on the `personal-db` parent command. It must appear **before** the subcommand:
- ✅ `personal-db --root /tmp/foo init`
- ❌ `personal-db init --root /tmp/foo` (rejected by typer)

Without `--root`, the data root defaults to `~/personal_db`.

## Verify

In Claude Code:
- "What trackers do I have?" → calls `list_trackers`
- "How many commits did I push last week?" → calls `query` or `get_series` against `github_commits`
- "Log that I meditated today" → calls `log_event("habits", …)`
- "/insights weekly review" → runs the skill, writes `notes/YYYY-MM-DD-weekly-review.md`

## Layout

See `docs/superpowers/specs/2026-04-25-personal-db-v0-design.md` for the full design.
See `docs/superpowers/plans/2026-04-25-personal-db-v0.md` for the implementation plan.
