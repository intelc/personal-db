# Packaging: freeze -> build shell -> sign -> notarize -> DMG

The full distribution story for PersonalDB.app. Prior art: this is the same
shape as [Screenpipe's](https://github.com/mediar-ai/screenpipe) Tauri
shell + frozen-sidecar architecture (a Rust/Tauri UI wrapping a separately
frozen background service, signed as one unit).

Status: steps 1-3 and 5 are implemented and **verified against a real
Developer ID certificate** (first signed build: 2026-07-17, identity
`Developer ID Application: Yiheng Chen (T78LM3Z7A5)`). Sidecar wiring into
the Tauri bundle is done (`shell/src-tauri/tauri.conf.json`
`bundle.externalBin` + `bundle.resources`, spawn logic in
`shell/src-tauri/src/daemon.rs`). Step 4 (notarize + staple) is scripted
and structurally exercised (the credential-gating and skip path ran for
real) but the actual `notarytool submit` has not run yet — it needs a
one-time `store-credentials` setup; see "The one remaining manual step"
at the bottom.

The exact commands that produced the first signed build, end to end:

```bash
# 1. freeze (rebuilds the wheel from the current tree)
./packaging/freeze-daemon.sh

# 2. release build; tauri.conf.json's bundle.macOS.signingIdentity makes
#    Tauri sign the app + main executable + sidecar launcher itself (~20s)
cd shell && npm run tauri build && cd ..

# 3. deep re-sign of the frozen payload inside the bundle (+ the standalone
#    payload dir); sign-only mode -- no notarization credentials set.
#    Took 73s for ~350 Mach-O files (each signature does a network round
#    trip to Apple's timestamp server).
IDENTITY="Developer ID Application: Yiheng Chen (T78LM3Z7A5)" \
  ./packaging/sign-and-notarize.sh

# 5. DMG around the signed .app (hdiutil, NOT tauri's dmg target -- see
#    build-dmg.sh's header for why); also signs the DMG itself
IDENTITY="Developer ID Application: Yiheng Chen (T78LM3Z7A5)" \
  ./packaging/build-dmg.sh
# -> packaging/build/PersonalDB_0.1.0_aarch64.dmg (136M)
```

Acceptance checks after step 3 (both behave exactly as they should for a
signed-but-not-yet-notarized Developer ID app):

```
$ codesign --verify --deep --strict PersonalDB.app
PersonalDB.app: valid on disk
PersonalDB.app: satisfies its Designated Requirement

$ spctl -a -vv -t exec PersonalDB.app
PersonalDB.app: rejected            <- EXPECTED until notarized
source=Unnotarized Developer ID
origin=Developer ID Application: Yiheng Chen (T78LM3Z7A5)
```

## The five steps

### 1. Freeze the daemon

```bash
./packaging/freeze-daemon.sh
```

Downloads a checksum-pinned python-build-standalone CPython 3.11, builds
the repo's wheel with `uv build`, and `uv pip install`s it (with the
finance+xhs extras — see the script's "batteries included" comment) into
the embedded interpreter. Output: `packaging/build/payload/`, a relocatable
directory containing `python/` (the interpreter + all dependencies) and
`personal-db-daemon-aarch64-apple-darwin` (a thin launcher script). See the
script's own comments for the full rationale and `--clean`/`--skip-verify`
flags.

Current payload size: ~165M (verified by actually running the script; see
its VERIFY step, which runs the frozen daemon on a scratch root and curls
`/api/v1/health`).

### 2. Build the shell

```bash
cd shell
npm install
npm run tauri build
```

See `shell/README.md`. **Sidecar wiring: DONE.** `tauri.conf.json` now has
exactly the shape sketched in earlier drafts of this README:

```jsonc
"bundle": {
  "externalBin": ["../../packaging/build/payload/personal-db-daemon"],
  // Tauri appends "-<target-triple>" itself when resolving which
  // platform binary to copy in, matching the launcher's actual filename;
  // in the bundle it lands at Contents/MacOS/personal-db-daemon.
  "resources": {
    "../../packaging/build/payload/python": "python"
    // lands at Contents/Resources/python/
  }
},
```

The launcher's relative-path problem was solved in the launcher itself
(`freeze-daemon.sh` step 4): it now probes `$DIR/python` (sibling — the
bare payload dir layout, and Tauri *dev* builds, which flatten externalBin
and resources into the same `target/debug/` dir) and falls back to
`$DIR/../Resources/python` (the real `.app` layout, `Contents/MacOS/` vs
`Contents/Resources/`). Verified both ways: the standalone payload health
check in `freeze-daemon.sh --verify`, and running
`PersonalDB.app/Contents/MacOS/personal-db-daemon dev daemon run` directly
from inside the built, signed bundle (came up healthy on a scratch root —
which also proves the hardened-runtime-signed interpreter still imports
its adhoc-to-Developer-ID-resigned extension modules).

The Rust side (`shell/src-tauri/src/daemon.rs::try_start_sidecar`) spawns
the sidecar via `tauri-plugin-shell` when the launch health check fails
and a sidecar is actually configured for the build; the daemon-down
guidance page remains the fallback when the spawn fails or times out.
Verified via `tauri dev` with `PERSONAL_DB_DAEMON_URL` pointed at a free
port and `PERSONAL_DB_ROOT` at a scratch dir.

### 3. Sign

Two layers, both verified on the first real signed build:

**Layer 1 — Tauri's own signing during `npm run tauri build`.**
`tauri.conf.json` sets `bundle.macOS.signingIdentity`, `entitlements`
(pointing at `packaging/entitlements.plist`), and `hardenedRuntime: true`,
so the release build itself signs the app bundle, main executable, and the
sidecar launcher with the real identity (~20s). But that is a *top-level*
pass only — everything under `Contents/Resources/python/` comes out still
carrying python-build-standalone's adhoc signatures, which local Gatekeeper
tolerates (`disable-library-validation` lets the interpreter dlopen them)
but Apple's notary service will not.

**Layer 2 — the deep re-sign:**

```bash
IDENTITY="Developer ID Application: Your Name (TEAMID)" \
./packaging/sign-and-notarize.sh
```

Signs every Mach-O in the frozen payload inside-out (extension modules,
then the interpreter, then everything under the bundle's
`Contents/MacOS` + `Contents/Resources`), then the app bundle itself —
hardened runtime, `packaging/entitlements.plist`, explicitly **no
`--deep`** on the signing side (Apple's own guidance: `--deep` papers over
signing-order bugs; this script gets the order right instead). Notarization
credentials are optional: with only `IDENTITY` set it signs, verifies, and
stops — that's the mode the first signed build used. 73s for ~350 files.

Two bugs the first real run found and fixed (details in the script header):
the original entitlements.plist had `--` inside its XML comment, which
Apple's strict plist parser rejects (`codesign` failed with a bare "syntax
error near line 19"); and the bundle-signing loop filtered candidates on
the executable permission bit, which would have silently skipped the ~85%
of payload `.so`/`.dylib` files that pip installs without `+x` — it now
classifies by content (`file` reports Mach-O) instead.

If codesign ever hangs at this step on a fresh machine: that's the keychain
access prompt waiting for a click — run `codesign` once interactively (or
click "Always Allow" on the prompt) and re-run. On this machine no prompt
appeared (the identity's key already trusted codesign).

### 4. Notarize

Also handled by `sign-and-notarize.sh` (steps 4-6 in that script), and
**only when credentials are present in the environment**: zips with
`ditto`, submits via `notarytool submit --wait`, staples the ticket.
Preferred setup is a one-time `notarytool store-credentials` keychain
profile (`KEYCHAIN_PROFILE` env var) so the app-specific password never has
to be in the script's environment — see "The one remaining manual step"
below for the exact commands. This stage has **not** run for real yet (no
credential profile existed on the machine that produced the first signed
build).

### 5. DMG

```bash
IDENTITY="Developer ID Application: Your Name (TEAMID)" \
./packaging/build-dmg.sh
# -> packaging/build/PersonalDB_<version>_<arch>.dmg
```

`hdiutil`-based (`packaging/build-dmg.sh`), deliberately **not** Tauri's
own `"dmg"` bundle target: `tauri build` with a dmg target re-bundles the
.app from scratch, which would clobber step 3's deep re-signing. The script
stages the signed .app (via `ditto`, preserving signatures) with an
`/Applications` symlink, builds a UDZO image named the way Tauri would name
it, and codesigns the DMG itself when `IDENTITY` is set. Sign-then-notarize
order matters: notarize the signed **.app**, staple it, *then* rebuild the
DMG around the already-stapled app (a DMG built before stapling needs its
own separate notarization pass, since Gatekeeper checks the outer disk
image too — the 2026-07-17 DMG is exactly such a pre-notarization build,
fine for local testing, rebuild it after notarizing).

## TCC / Full Disk Access notes

This is the actual product reason the whole Tauri-shell-plus-signing effort
exists, not a side detail:

- **TCC (macOS's privacy permission system) grants Full Disk Access to a
  specific signed binary identity**, not to "personal_db" as a concept.
  Today (unsigned/CLI install), that identity is whatever Python
  interpreter runs `personal-db daemon run` — for most users, their
  homebrew or pyenv Python. Two problems fall out of this:
  1. **The FDA prompt/pane shows "Python" (or a version-numbered binary
     path)**, not "PersonalDB" — deeply confusing for a non-developer user
     trying to grant access to "the app they just installed."
  2. **`brew upgrade python` silently breaks it.** Homebrew's Python
     formula gets replaced on upgrade; the binary at
     `/opt/homebrew/.../Python` that macOS remembers granting FDA to is
     gone, and the *new* Python at the same path is, as far as TCC is
     concerned, a different, ungranted binary. Users see trackers silently
     stop working with no obvious cause.
- **The signed .app fixes both.** Once PersonalDB.app is signed with a
  stable Developer ID identity and the frozen daemon sidecar runs *inside*
  that signed bundle (not some ambient interpreter), FDA is granted to
  "PersonalDB" by name, and it survives every future PersonalDB update as
  long as the same Developer ID keeps signing it — no homebrew upgrade, no
  reinstall, no re-grant. This is *the* single biggest UX win of doing all
  of this work, bigger than the menu bar UI itself.
- **A stable signing identity on every build is non-negotiable**, including
  dev builds if you're testing FDA flows locally: ad-hoc signing (`-`) or
  no signing at all produces a binary with no stable identity, so TCC
  treats every rebuild as a brand-new, ungranted app (`shell/README.md`
  calls this out too, for the unsigned builds this milestone actually
  produces).
- There is **no supported "do I have FDA" API** — the standard workaround
  (already implemented in `core/permissions.py` / `wizard/steps.py`'s
  `fda_check` flow, which the signed app's first-run onboarding should
  reuse pointing at PersonalDB.app instead of a Python path) is to probe a
  path FDA actually gates (e.g. `~/Library/Application
  Support/com.apple.TCC/TCC.db` itself, or a Messages/Mail/Safari data
  file) and treat a `PermissionError` as "not granted."

## Release checklist

1. Bump the version — single source is `pyproject.toml`'s `[project]
   version`; propagate it into `shell/src-tauri/tauri.conf.json`'s
   `"version"` field and its `Cargo.toml`'s `[package] version` at build
   time (not automated yet — do this by hand until a build script wires it
   up).
2. **Bump `CFBundleVersion`** (the build number, not just the marketing
   version) — macOS/Gatekeeper/Sparkle-style updaters use this to decide
   "is this newer," and reusing one across two different signed builds
   can confuse update checks and notarization's own ticket cache. Tauri
   derives `CFBundleVersion` from `tauri.conf.json`'s `"version"` by
   default; if you ever need them to diverge (e.g. multiple signed
   respins of the same marketing version), that requires a build-time
   `Info.plist` post-processing step — not needed yet, not covered by this
   milestone.
3. Run `packaging/freeze-daemon.sh --clean` fresh (don't reuse a stale
   payload across a dependency bump).
4. `npm run tauri build` (release profile) in `shell/`.
5. `packaging/sign-and-notarize.sh` with the release identity — with
   `KEYCHAIN_PROFILE` set so notarize+staple actually run (see below).
6. `packaging/build-dmg.sh` with `IDENTITY` set — after stapling, so the
   DMG wraps the stapled .app.
7. Smoke-test on a **second Mac** (or fresh VM) per the Phase 4 plan's
   verification criteria: FDA prompt names PersonalDB.app, `spctl -a -vv`
   and `codesign --verify --deep --strict` pass, and — the real test of
   signing stability — updating from a previous signed build preserves the
   existing FDA grant.

## The one remaining manual step: notarization credentials

Everything up to and including signing + DMG has now run for real. The
only piece that hasn't is notarize+staple, because it needs an Apple ID
app-specific password, which must be created and stored by a human, once:

1. Generate an app-specific password at
   [account.apple.com](https://account.apple.com) → Sign-In and Security →
   App-Specific Passwords (do **not** use the real Apple ID password).
2. Store it in the keychain as a notarytool profile (interactive; the
   password is prompted for, never lands in shell history):

   ```bash
   xcrun notarytool store-credentials personal-db-notary \
     --apple-id <your-apple-id-email> \
     --team-id T78LM3Z7A5
   ```

3. Re-run the sign script with the profile — it will sign again (cheap,
   ~1 min), then zip, submit, wait, and staple:

   ```bash
   IDENTITY="Developer ID Application: Yiheng Chen (T78LM3Z7A5)" \
   KEYCHAIN_PROFILE=personal-db-notary \
   ./packaging/sign-and-notarize.sh
   ```

4. Rebuild the DMG around the now-stapled .app:

   ```bash
   IDENTITY="Developer ID Application: Yiheng Chen (T78LM3Z7A5)" \
   ./packaging/build-dmg.sh
   ```

After step 3, `spctl -a -vv -t exec PersonalDB.app` should flip from
`rejected (Unnotarized Developer ID)` to `accepted, source=Notarized
Developer ID` — that's the whole acceptance test.
