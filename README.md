# personal_db

The missing open-source database + data-sync piece for self-hosted agentic second-brain systems.

Where Obsidian holds your notes, `personal_db` holds your structured data — sleep, code, messages, screen time, contacts, calendar — in a local SQLite file, and exposes it to AI agents over MCP so they have persistent memory of you across tools (Claude Desktop, Claude Code, OpenClaw, Cursor, …).

SQLite + per-tracker ingest scripts + MCP server. macOS only in v0.

## Install

```bash
curl -LsSf https://raw.githubusercontent.com/intelc/personal-db/main/install.sh | bash
```

This installs [`uv`](https://github.com/astral-sh/uv) if you don't have it, then `uv tool install`s `personal-db`. After install:

```bash
personal-db setup
```

`setup` initializes the data root, then asks how you want to configure trackers — **Browser** (visual wizard at http://127.0.0.1:8765/setup), **Terminal** (questionary prompts), or **Skip**.

**Tip:** download and run the script directly to have the wizard launch automatically:

```bash
curl -LsSf https://raw.githubusercontent.com/intelc/personal-db/main/install.sh -o install.sh
bash install.sh
```

Set `PERSONAL_DB_NO_SETUP=1` to opt out of the auto-launched wizard.

### From source (for development)

```bash
git clone https://github.com/intelc/personal-db
cd personal-db
./scripts/install_dev.sh
source .venv/bin/activate
```

## Quick start

`personal-db setup` is the only command you need to run after install. It walks through tracker selection, configuration, scheduler install, and agent wire-up in one flow.

```bash
personal-db setup
```

You'll be asked which mode you want:

- **Browser** (recommended) — opens a visual wizard at `http://127.0.0.1:8765/setup` with a tracker list, form-based per-tracker setup, and click-through buttons for finalize steps.
- **Terminal** — questionary-driven prompts in your shell.
- **Skip** — exits cleanly; run `personal-db setup` again whenever you're ready.

After you finish configuring trackers, finalize steps run automatically:

1. **Scheduler** — installs a launchd job (`~/Library/LaunchAgents/com.personal_db.scheduler.plist`) that runs `personal-db sync --due` every 10 minutes.
2. **MCP server** — auto-installs `personal_db` into the agents you choose (Claude Code, Claude Desktop, Cursor). Behind the scenes this calls `claude mcp add` (Code), or merges into `~/Library/Application Support/Claude/claude_desktop_config.json` (Desktop), or `~/.cursor/mcp.json` (Cursor).
3. **Dashboard** (optional) — offers to launch the menu bar + dashboard via `personal-db ui`. Default is skip; agents read your data over MCP regardless of the dashboard being open.

### After setup

```bash
# Pull historical data once (the scheduler only handles incremental sync going forward)
personal-db backfill github_commits
personal-db backfill whoop

# Install the /insights skill into Claude Code (one-time)
mkdir -p ~/.claude/skills/personal-db
cp src/personal_db/templates/claude_skill/insights.md ~/.claude/skills/personal-db/

# Open the dashboard whenever you want
personal-db ui

# Add MCP into another agent later
personal-db mcp install              # interactive picker
personal-db mcp install cursor       # non-interactive single target
```

### Re-running setup

`personal-db setup` is idempotent. Run it any time to add a new tracker, rotate a credential, or re-enable the scheduler. Existing values are shown as defaults (secrets masked) so you can press Enter to keep them.

## CLI argument order note

`--root` is a *global* option on the `personal-db` parent command. It must appear **before** the subcommand:
- ✅ `personal-db --root /tmp/foo init`
- ❌ `personal-db init --root /tmp/foo` (rejected by typer)

Without `--root`, the data root defaults to `~/personal_db`.

## Credentials

Credentials live in `<root>/.env` (default `~/personal_db/.env`, mode 0600).
The file is loaded automatically on every `personal-db` invocation; shell
environment variables override `.env` values (useful for debugging and tests).

To rotate a credential, re-run `personal-db setup` and reconfigure the
relevant tracker — current values are shown as defaults (secrets are masked).
Or jump straight to a single tracker via `personal-db tracker setup <name>`.

## Verify

In Claude Code:
- "What trackers do I have?" → calls `list_trackers`
- "How many commits did I push last week?" → calls `query` or `get_series` against `github_commits`
- "Log that I meditated today" → calls `log_event("habits", …)`
- "/insights weekly review" → runs the skill, writes `notes/YYYY-MM-DD-weekly-review.md`

## github_commits — capturing local-CLI commits

The setup wizard asks for your GitHub token and then optionally for the email
addresses you commit with. You can also set or update `GITHUB_AUTHOR_EMAILS`
manually at any time.

By default, `github_commits` matches commits via GitHub's standard email-to-user
linkage (your GitHub login is derived automatically from the token). If you
commit locally with an email that isn't on your GitHub account
(`git config user.email`), those commits won't be attributed to you on GitHub
and won't be captured.

To include them, set `GITHUB_AUTHOR_EMAILS` in `<root>/.env` (the wizard also
prompts for this during setup):

```bash
echo 'GITHUB_AUTHOR_EMAILS=you@example.com,you@work.com' >> ~/personal_db/.env
```

Comma-separated; case-insensitive. Find your local email with:

```bash
git config user.email
```

## Layout

See `docs/superpowers/specs/2026-04-25-personal-db-v0-design.md` for the full design.
See `docs/superpowers/plans/2026-04-25-personal-db-v0.md` for the implementation plan.
