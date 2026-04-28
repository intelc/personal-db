# Install demo — shows the one-line `bash <(curl ...)` install.
#
# Pairs with the Install section of the README. Stops once the wizard's
# picker appears so the viewer sees what comes next without sitting through
# the full setup arc (covered separately in setup-demo.tape).
#
# Side effects:
#   - Installs personal-db globally via `uv tool install --force`. Run
#     `uv tool uninstall personal-db` to undo.
#   - Creates /tmp/personal_db_demo (left empty since we exit at the picker).
#
# Re-record: `vhs docs/demos/install-demo.tape` from the project root.

Output docs/demos/install-demo.gif

Set Shell "bash"
Set Width 1300
Set Height 600
Set FontSize 18
Set Theme "Dracula"
Set Padding 20
Set TypingSpeed 50ms
Set WindowBar Colorful

# Hidden setup: env vars to keep the demo from touching real state.
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

# uv tool install + auto-launch of `personal-db setup` (~15-18s)
Sleep 18s

# Picker is up — hold so viewer can read the three options
Sleep 2s
Ctrl+C
Sleep 1s
