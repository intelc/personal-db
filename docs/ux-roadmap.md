# UX roadmap — consumer/prosumer polish

Target user: OpenClaw-adjacent, AI-curious prosumers — comfortable pasting an
API key, not interested in reading YAML. Goals, in the product's words:
**clear, understandable, reliable, predictable, extensible.**

Status key: `[ ]` not started · `[~]` in progress · `[x]` done

## P0 — a stranger's first ten minutes

- [x] **1. First-run experience.** Land on Setup (or a welcome pane) until at
  least one tracker is Ready; dashboard empty state becomes a real
  "connect your first source" CTA. State the local-first promise in one
  sentence ("everything lives in `~/personal_db` on this Mac; nothing
  leaves it") on first run.
- [x] **2. Empty states that instruct.** Every viz/app "No data" should say
  *why* it's empty and what to do next: "first sync backfills 90 days,
  check back shortly" vs "needs Full Disk Access → grant" vs "requires the
  Plaid tracker → install".
- [x] **3. Kill the terminal cliff in setup.** No tracker setup should require
  a terminal. Extend the daemon's browser OAuth flow (`/setup/oauth/{name}`)
  to cover every OAuth tracker (Whoop et al.).
- [x] **5. Surface sync failures.** (visual tray-badge check pending next app build) Three tiers: (a) Health pane — last error
  per tracker from `state/sync_errors.jsonl`, retry button, log tail;
  (b) inline error text under the Sync button instead of a hover tooltip;
  (c) menu-bar tray badge on repeated failures (Tauri side, later).

## P1 — calm and predictable

- [x] **6. Daemon-down UX.** The Tauri shell shows "PersonalDB isn't running —
  Restart" instead of a failed white page; surface daemon version.
- [x] **7. Visible schedules.** Cards say "syncs every 6h · next in ~2h";
  global pause toggle; "Sync all now".
- [x] **8. No spooky global writes.** Any action that writes outside
  `<root>` (launchd, `~/.claude/settings.json`, MCP configs) is guarded
  from scratch roots (core/global_writes.py, merged) — remaining: say so
  on the button label/confirm in the UI.
- [ ] **9. Update story.** BLOCKED on: (a) real notarization run (one-time
  `notarytool store-credentials`, human step — see packaging/README.md), and
  (b) a release-artifact host for the update manifest. Then:
  tauri-plugin-updater + separate Ed25519 signing key + "What's new"; DB
  migrations logged visibly in Health.

## P2 — extensibility as a feature

- [x] **10. Dashboard editing in the UI.** Replace hand-edited
  `dashboard.yaml` with a toggle-and-reorder viz picker (server-rendered,
  no framework).
- [ ] **4. Data browser.** Read-only per-tracker table viewer (AG Grid is
  already vendored) answering "what has it collected about me"; doubles as
  the debugging surface.
- [ ] **11. Custom sources as a product feature.** In-app "Add your own
  source" that scaffolds `dev tracker new` output + publish docs for the
  stable `personal_db.ui` SDK.
- [ ] **12. Lead with the agent.** Ship the agent drawer enabled with a
  one-time explainer; make the "ask about this element" pointer
  discoverable; aim docs at "ask the agent to build you a tracker".

## Notes

- Numbering matches the original assessment (4 sits in P2 despite its
  number; ordering here is priority, not index).
- Post-merge SOP reminder: template/`actions.py` edits require
  `personal-db tracker reinstall <name>` on live roots (CLAUDE.md).
