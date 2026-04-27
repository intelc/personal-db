# Hero demo — full install + setup arc.
#
# Shows: typing the one-line curl install → uv tool install pulls personal-db
# from GitHub → setup wizard auto-launches → picker → Terminal mode → tracker
# menu → install life_context → test sync + backfill spawn → finalize block
# (scheduler skipped, MCP skipped, dashboard skipped) → try-it prompt.
#
# Side effects:
#   - Installs personal-db globally via `uv tool install --force` (writes
#     ~/.local/bin/personal-db). Run `uv tool uninstall personal-db` to undo.
#   - Creates /tmp/personal_db_demo. Doesn't touch ~/personal_db.
#
# Re-record from the project root: `vhs docs/demos/setup-demo.tape`.

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
#   - PERSONAL_DB_NO_SCHEDULER=1 keeps finalize from clobbering the real
#     ~/Library/LaunchAgents/com.personal_db.scheduler.plist.
#   - PERSONAL_DB_ROOT=/tmp/personal_db_demo keeps wizard data out of
#     ~/personal_db (the real user root).
Hide
Type "export PERSONAL_DB_NO_SCHEDULER=1 PERSONAL_DB_ROOT=/tmp/personal_db_demo PS1='> '"
Enter
Type "rm -rf /tmp/personal_db_demo && clear"
Enter
Sleep 500ms
Show

Type "bash <(curl -LsSf https://raw.githubusercontent.com/intelc/personal-db/main/install.sh)"
Sleep 500ms
Enter

# uv tool install pulls from git; ~10-18s on a typical machine
Sleep 18s

# Picker auto-launches (post-install). Highlight Terminal.
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

# Test sync runs (no-op for life_context, manual capture), backfill spawns,
# we land back at the menu with life_context now in the installed section
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

# Try-it prompt — the money frame
Sleep 6s

# Hold the final frame
Sleep 3s
