# Setup-wizard demo — full arc from `personal-db setup` to the try-it prompt.
#
# Pairs with the Quick start section of the README. Assumes personal-db is
# already installed (skip the curl install — that's covered in install-demo.tape).
#
# Side effects: creates /tmp/personal_db_demo. Doesn't touch ~/personal_db
# or the launchd plist.
#
# To re-record on a different machine: replace the alias path below with
# wherever your `personal-db` binary lives (e.g. `which personal-db`).
# Run from the project root: `vhs docs/demos/setup-demo.tape`.

Output docs/demos/setup-demo.gif

Set Shell "bash"
Set Width 1300
Set Height 800
Set FontSize 18
Set Theme "Dracula"
Set Padding 20
Set TypingSpeed 50ms
Set WindowBar Colorful

# Hidden setup:
#   - Alias `personal-db` to the dev venv binary (skip activate to avoid
#     "(personal_db)" prompt prefix).
#   - PERSONAL_DB_NO_SCHEDULER=1 keeps the demo from clobbering the real
#     ~/Library/LaunchAgents/com.personal_db.scheduler.plist.
#   - PERSONAL_DB_ROOT=/tmp/personal_db_demo keeps wizard data out of
#     ~/personal_db.
Hide
Type "alias personal-db='/Users/yihengchen/codestuff/aiexperiments/personal_db/.venv/bin/personal-db'"
Enter
Type "export PERSONAL_DB_NO_SCHEDULER=1 PERSONAL_DB_ROOT=/tmp/personal_db_demo PS1='> '"
Enter
Type "rm -rf /tmp/personal_db_demo && clear"
Enter
Sleep 500ms
Show

Type "personal-db setup"
Sleep 500ms
Enter

# Init + picker render
Sleep 3s

# Highlight Terminal (default is Browser, one Down)
Down
Sleep 800ms
Enter

# Wizard menu renders the bundled tracker list
Sleep 3s

# Navigate to life_context (8 down from chrome_history at the top)
Down
Sleep 200ms
Down
Sleep 200ms
Down
Sleep 200ms
Down
Sleep 200ms
Down
Sleep 200ms
Down
Sleep 200ms
Down
Sleep 200ms
Down
Sleep 500ms
Enter

# life_context: one instructions step → acknowledge with Enter
Sleep 1s
Enter

# Test sync passes (no-op for life_context), backfill spawns, back at menu
Sleep 4s

# Up wraps to the bottom item (Done — exit wizard)
Up
Sleep 600ms
Enter

# Finalize block: scheduler skipped, then MCP install prompt
Sleep 2s
Type "n"
Sleep 200ms
Enter

# Dashboard prompt: accept the default (No)
Sleep 1s
Enter

# Try-it prompt — the closing frame
Sleep 6s
Sleep 3s
