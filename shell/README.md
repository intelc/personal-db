# PersonalDB shell (Tauri)

A menu-bar app that wraps the existing `personal-db` daemon. It does not run
any personal_db Python itself — it is a thin client: a tray icon plus a
system WebView window pointed at the daemon's own dashboard
(`http://127.0.0.1:8765/`). All the actual sync/read/write logic still lives
in the daemon (`src/personal_db/services/daemon/`); this app only replaces
`services/ui/menubar.py` (the `rumps` menu bar) with a cross-platform-capable,
signable, notarizable native shell.

This is the **Phase 4 first milestone**: unsigned, locally buildable, wraps
whatever daemon is already running on the machine. The frozen/sidecar daemon
(`packaging/freeze-daemon.sh`) and the signing/notarization pipeline
(`packaging/sign-and-notarize.sh`) are separate, later steps — see
`packaging/README.md`.

## What it does

- **Tray icon** (no Dock icon — `ActivationPolicy::Accessory` on macOS) with:
  - **Open Dashboard** — resolves the daemon, authenticates, and opens the
    dashboard in a window.
  - **Sync Now** — triggers `sync_due` on the daemon and shows the result as
    a native notification.
  - **Status** — currently just opens the dashboard (there's no separate
    native status view yet; the dashboard itself is the status view).
  - **Start at Login** — a checkable item that toggles autostart via
    `tauri-plugin-autostart`. This is an **interim** mechanism; see
    `packaging/smappservice/RegisterAgent.swift` for the SMAppService-based
    replacement planned once the app is signed.
  - **Quit**.
- **On first launch**, and whenever "Open Dashboard"/"Status" is clicked, the
  Rust core (`src-tauri/src/daemon.rs`):
  1. Resolves the personal_db root: `$PERSONAL_DB_ROOT`, else `~/personal_db`.
  2. Resolves the daemon base URL: `$PERSONAL_DB_DAEMON_URL`, else
     `http://127.0.0.1:8765`.
  3. Reads `<root>/state/daemon.token`.
  4. `GET /api/v1/health` (this route is auth-exempt on the daemon side).
     - **Daemon down / unreachable / unhealthy** → opens the window on the
       bundled local page `web/daemon-down.html`, with the resolved base URL
       and failure reason as query params, and a **Retry** button that calls
       back into Rust (via the `open_dashboard` Tauri command) to re-run this
       whole flow.
     - **Daemon up** → `POST /api/v1/auth/otc` with `Authorization: Bearer
       <token>`, gets a single-use 30-second one-time code, and navigates the
       window straight to
       `http://127.0.0.1:8765/auth/bootstrap?otc=<code>&next=/` — the daemon
       redeems the code, sets the `pdb_session` cookie, and redirects to `/`.
       If no token file exists yet, or the OTC mint fails for some reason,
       it falls back to `http://127.0.0.1:8765/auth?next=/` (the manual
       paste-the-token page the daemon already serves).

The daemon's own long-lived token **never enters JS-land or a page URL**:
every token-bearing HTTP call (health, OTC mint, sync_due) is made from Rust
via `reqwest`, not `fetch()` inside the WebView. The only thing that ever
reaches the WebView is the single-use, 30-second OTC bootstrap URL, which is
useless to anyone after first use (or after 30 seconds).

## Dev loop

```bash
cd shell
npm install
npm run tauri dev
```

This starts the app pointed at whatever `web/` currently contains (there's no
frontend build step — `web/index.html` and `web/daemon-down.html` are static,
hand-written HTML/CSS/vanilla JS; no bundler, no framework). Edits to those
files are picked up on reload; edits to `src-tauri/src/*.rs` trigger a Rust
rebuild.

The app assumes you already have a personal_db daemon running (see the root
README / `personal-db daemon install`). It does not start, stop, or manage
the daemon process in this milestone — it only talks to whatever's listening
on the resolved base URL.

## Build (unsigned)

```bash
npm run tauri build -- --debug   # faster, unoptimized, for local testing
# or
npm run tauri build              # release profile, still unsigned
```

Produces `src-tauri/target/{debug,release}/bundle/macos/PersonalDB.app`.
Since it's unsigned (ad-hoc signed by default, or not signed at all
depending on your local `codesign` setup), macOS Gatekeeper will complain the
first time you open it (right-click → Open, or `xattr -d
com.apple.quarantine` on it), and any Full Disk Access grant you give it will
almost certainly **not survive a rebuild** — ad-hoc/unsigned binaries don't
have a stable identity, so TCC treats every rebuild as a new app. This is
exactly the pain the signed pipeline in `packaging/` exists to fix.

## Current limitations (this milestone only)

- **Unsigned.** No Developer ID signature, no notarization, no hardened
  runtime entitlements. See `packaging/sign-and-notarize.sh` and
  `packaging/README.md` for the documented (but untested — no cert available
  in the environment that built this) pipeline that fixes this.
- **Sidecar daemon is wired in.** `tauri.conf.json`'s `bundle.externalBin`
  points at `packaging/build/payload/personal-db-daemon` (Tauri resolves the
  `-aarch64-apple-darwin`-suffixed file itself) and `bundle.resources` ships
  the frozen `python/` tree alongside it (`packaging/freeze-daemon.sh`
  produces both). `daemon.rs::try_start_sidecar` spawns it via
  `tauri-plugin-shell` when the initial health check fails and this build
  actually has a sidecar configured (dev builds run without the payload at
  the expected relative path just skip this and fall straight to the
  guidance page, same as before). Root is `$PERSONAL_DB_ROOT` (else
  `~/personal_db`); port comes from `$PERSONAL_DB_DAEMON_URL` (else the
  default 8765). The launcher script itself
  (`packaging/build/payload/personal-db-daemon-*`) looks for its embedded
  `python/` first as a sibling directory, then falls back to
  `../Resources/python` — the latter is what a real signed `.app` needs,
  since `bundle.externalBin` lands the launcher in `Contents/MacOS/` while
  `bundle.resources` lands `python/` in `Contents/Resources/` (siblings
  under `Contents/`, not siblings of each other). Verified end-to-end via
  `tauri dev` with `PERSONAL_DB_DAEMON_URL` pointed at a free port and
  `PERSONAL_DB_ROOT` at a scratch dir: the sidecar spawned, bound the
  scratch port, and wrote `db.sqlite`/`state/` under the scratch root.
  For FDA purposes this app still targets whatever Python interpreter runs
  the daemon (the embedded one, once spawned this way) — the signed app +
  frozen sidecar is what gets FDA prompting a stable, app-scoped identity
  instead of an ambient homebrew/pyenv Python.
- **"Start at Login" is `tauri-plugin-autostart`**, a LaunchAgent-plist-based
  mechanism that works fine unsigned. The SMAppService-based replacement
  (`packaging/smappservice/`) is reference code only for now — not wired
  into this build. See `packaging/README.md` for why (SMAppService's value
  — reliable status polling, cleaner uninstall — is really only realized
  once the daemon ships as a signed sidecar inside a signed bundle).
- **"Status" doesn't have its own native view** — it just opens the
  dashboard, which already shows tracker/sync status. A dedicated
  lightweight status popover is a nice-to-have for later, not required by
  this milestone's scope.

## Files

```
shell/
  package.json            npm scripts + @tauri-apps/cli, notification/autostart JS bindings
  web/                     static frontend (frontendDist) — no build step
    index.html             fallback splash (normally never seen; see daemon.rs)
    daemon-down.html        "daemon not running" guidance page + Retry button
  src-tauri/
    Cargo.toml
    build.rs
    tauri.conf.json        productName "PersonalDB", identifier com.personal-db.app
    capabilities/default.json
    icons/                  generated via `npx tauri icon icon-source.png`
    icon-source.png         placeholder 1024x1024 source icon (swap before a real release)
    src/
      main.rs               tray menu, activation policy, autostart toggle
      daemon.rs             daemon HTTP client + window bootstrap flow
```
