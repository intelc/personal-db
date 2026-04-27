# Hero demo — `personal-db setup` from prompt to wizard menu.
# Side effects: creates /tmp/personal_db_demo. Doesn't touch ~/personal_db.
#
# To re-record this on a different machine, ensure `personal-db` is on PATH
# (e.g. via `uv tool install personal-db`) and remove the venv-activate line
# in the Hide block below.

Output setup-demo.gif

Set Width 1100
Set Height 800
Set FontSize 14
Set Theme "Dracula"
Set Padding 20
Set TypingSpeed 50ms

# Quietly source the dev venv (this repo), opt out of the global launchd
# scheduler install (PERSONAL_DB_NO_SCHEDULER=1 — the plist lives at a global
# path so we don't want demo runs touching it), and clean any prior demo state.
Hide
Type "source ~/codestuff/aiexperiments/personal_db/.venv/bin/activate && export PERSONAL_DB_NO_SCHEDULER=1 && rm -rf /tmp/personal_db_demo && clear"
Enter
Sleep 500ms
Show

Type "personal-db --root /tmp/personal_db_demo setup"
Sleep 500ms
Enter

# Init + picker appears
Sleep 3s

# Pick Terminal (down once from default Browser)
Down
Sleep 800ms
Enter

# Wizard menu renders the bundled tracker list
Sleep 3s

# Navigate through the menu so the viewer sees it's interactive
Down
Sleep 350ms
Down
Sleep 350ms
Down
Sleep 350ms
Down
Sleep 500ms
Up
Sleep 500ms
Up
Sleep 500ms

# Hold the final frame for a beat, then exit cleanly
Sleep 1s
Ctrl+C
Sleep 1s
