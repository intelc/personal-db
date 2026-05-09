# Tape 1 (smoke test): show the setup wizard's three-way picker, then abort.
# Side effects: creates /tmp/personal_db_demo. Doesn't touch ~/personal_db.

Output setup-picker.gif

Set Width 1100
Set Height 500
Set FontSize 14
Set Theme "Dracula"
Set Padding 20
Set TypingSpeed 50ms

Hide
Type "cd /tmp && rm -rf personal_db_demo"
Enter
Sleep 200ms
Show

Type "personal-db --root /tmp/personal_db_demo setup"
Sleep 500ms
Enter

# Wait for init + the picker to render
Sleep 3s

# Move the cursor between options so the viewer sees it's interactive
Down
Sleep 800ms
Down
Sleep 800ms
Up
Sleep 800ms

# Abort cleanly without picking anything
Ctrl+C
Sleep 1s
