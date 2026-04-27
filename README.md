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

```bash
# Initialize the data root (default ~/personal_db)
personal-db init

# Install some built-in trackers
personal-db tracker install github_commits
personal-db tracker install whoop
personal-db tracker install screen_time
personal-db tracker install imessage
personal-db tracker install habits

# Configure each connector via the interactive wizard
personal-db tracker setup
# (Or set up one specific tracker: personal-db tracker setup whoop)
#
# The wizard:
#   - prompts for env vars (GITHUB_TOKEN, GITHUB_AUTHOR_EMAILS, WHOOP_CLIENT_ID, etc.)
#     and writes them to <root>/.env (mode 0600, gitignored)
#   - optional fields (like GITHUB_AUTHOR_EMAILS) can be skipped with Enter
#   - launches OAuth flows in your browser for OAuth-based connectors (whoop)
#   - probes Full Disk Access for chat.db / knowledgeC.db and opens System
#     Settings if needed
#   - runs a test sync after each connector to confirm it's working

# First run: backfill what's available
personal-db backfill github_commits
personal-db backfill whoop

# Install the launchd scheduler (runs `personal-db sync --due` every 10 min)
personal-db scheduler install

# Add the MCP server to Claude Code
# (use the absolute path — Claude Code spawns MCP servers with a minimal
# environment that does NOT inherit your shell's PATH, so a bare
# "personal-db" reference will fail to connect)
claude mcp add personal_db -- "$(which personal-db)" mcp

# Install the /insights skill
mkdir -p ~/.claude/skills/personal-db
cp src/personal_db/templates/claude_skill/insights.md ~/.claude/skills/personal-db/
```

## CLI argument order note

`--root` is a *global* option on the `personal-db` parent command. It must appear **before** the subcommand:
- ✅ `personal-db --root /tmp/foo init`
- ❌ `personal-db init --root /tmp/foo` (rejected by typer)

Without `--root`, the data root defaults to `~/personal_db`.

## Credentials

Credentials live in `<root>/.env` (default `~/personal_db/.env`, mode 0600).
The file is loaded automatically on every `personal-db` invocation; shell
environment variables override `.env` values (useful for debugging and tests).

To rotate a credential or fix a misconfiguration, re-run
`personal-db tracker setup <name>` — current values are shown as defaults
(secrets are masked) so you can press Enter to keep them or type a new value
to overwrite.

## Verify

In Claude Code:
- "What trackers do I have?" → calls `list_trackers`
- "How many commits did I push last week?" → calls `query` or `get_series` against `github_commits`
- "Log that I meditated today" → calls `log_event("habits", …)`
- "/insights weekly review" → runs the skill, writes `notes/YYYY-MM-DD-weekly-review.md`

## github_commits — capturing local-CLI commits

The setup wizard (`personal-db tracker setup github_commits`) asks for your
GitHub token and then optionally for the email addresses you commit with.
You can also set or update `GITHUB_AUTHOR_EMAILS` manually at any time.

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
