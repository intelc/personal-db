# Hero demo — `personal-db setup` from prompt to wizard menu.
# Side effects: creates /tmp/personal_db_demo. Doesn't touch ~/personal_db.
#
# To re-record on a different machine: replace the alias path below with
# wherever your `personal-db` binary lives (e.g. via `which personal-db`).
# Run from the project root: `vhs docs/demos/setup-demo.tape`

Output docs/demos/setup-demo.gif

Set Width 1100
Set Height 600
Set FontSize 14
Set Theme "Dracula"
Set Padding 20
Set TypingSpeed 50ms

# Setup, hidden from the recording:
#   - Alias `personal-db` to the dev venv's binary so we don't need to
#     activate the venv (which prepends "(personal_db)" to the prompt).
#   - PERSONAL_DB_NO_SCHEDULER=1 keeps the demo from clobbering the
#     real launchd plist at ~/Library/LaunchAgents/.
#   - PS1='> ' gives a clean, neutral prompt.
Hide
Type "alias personal-db='/Users/yihengchen/codestuff/aiexperiments/personal_db/.venv/bin/personal-db'"
Enter
Type "export PERSONAL_DB_NO_SCHEDULER=1 PS1='> '"
Enter
Type "rm -rf /tmp/personal_db_demo && clear"
Enter
Sleep 500ms
Show

Type "personal-db --root /tmp/personal_db_demo setup"
Sleep 500ms
Enter

# Init + picker render
Sleep 3s

# Highlight Terminal (default is Browser, one Down)
Down
Sleep 800ms
Enter

# Wizard menu appears
Sleep 3s

# Move the cursor through the menu so the viewer sees it's interactive
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

# Hold the final frame, then exit cleanly
Sleep 1s
Ctrl+C
Sleep 1s
