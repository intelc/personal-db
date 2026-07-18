# Packaging: freeze -> build shell -> sign -> notarize -> DMG

The full distribution story for PersonalDB.app. Prior art: this is the same
shape as [Screenpipe's](https://github.com/mediar-ai/screenpipe) Tauri
shell + frozen-sidecar architecture (a Rust/Tauri UI wrapping a separately
frozen background service, signed as one unit).

Status as of this milestone: steps 1-2 below are implemented and verified
(`packaging/freeze-daemon.sh`, `shell/`). Steps 3-5 are documented and
scripted (`packaging/sign-and-notarize.sh`) but **untested** — no Apple
Developer ID certificate was available in the environment that wrote them.
Treat them as a careful first draft, not a proven pipeline.

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

See `shell/README.md`. **Not done in this milestone:** wiring the frozen
payload from step 1 into the Tauri build as a sidecar. That needs
`shell/src-tauri/tauri.conf.json` to grow:

```jsonc
"bundle": {
  "externalBin": ["../../packaging/build/payload/personal-db-daemon"],
  // Tauri appends "-<target-triple>" itself when resolving which
  // platform binary to copy in, matching the launcher's actual filename.
  "resources": {
    "../../packaging/build/payload/python": "python"
    // lands at Contents/Resources/python/ — the launcher script would
    // need a small path fix (or a build-time relative-path rewrite) since
    // it currently assumes `python/` is a *sibling* of itself, not one
    // level up in Resources/ vs MacOS/. Left as an open problem for
    // whoever wires this in.
  }
},
```

and the Rust side would need a "start the sidecar on launch" path instead
of only "connect to whatever's already running on 127.0.0.1:8765" (today's
behavior — see `shell/README.md`'s limitations section). This is real,
non-trivial work, not a config one-liner — flagging it explicitly rather
than pretending step 2 already produces a fully self-contained app.

### 3. Sign

```bash
IDENTITY="Developer ID Application: Your Name (TEAMID)" \
APPLE_ID="you@example.com" TEAM_ID="TEAMID" APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" \
./packaging/sign-and-notarize.sh
```

Signs every Mach-O in the frozen payload inside-out (extension modules,
then the interpreter, then anything else the Tauri build placed in the
bundle), then the app bundle itself — hardened runtime,
`packaging/entitlements.plist`, explicitly **no `--deep`** (Apple's own
guidance: `--deep` papers over signing-order bugs; this script gets the
order right instead so it's never needed). See the script's header comment
for why this whole thing is marked UNTESTED, and its inline comments for
why each entitlement is needed (frozen CPython + C-extension specific,
not a generic hardened-runtime template).

### 4. Notarize

Also handled by `sign-and-notarize.sh` (steps 4-6 in that script): zips with
`ditto`, submits via `notarytool submit --wait`, staples the ticket. Requires
an [app-specific password](https://support.apple.com/en-us/102654) or a
`notarytool store-credentials` keychain profile (`KEYCHAIN_PROFILE` env var
— preferred once you've set one up once, so the password never has to be
in this script's environment).

### 5. DMG

Not scripted yet. Once step 2's sidecar wiring lands, `tauri build` can
produce a DMG directly (Tauri's macOS bundler supports `"targets": ["dmg"]`
in `tauri.conf.json`'s `bundle.targets` — currently set to `["app"]` only,
see `shell/src-tauri/tauri.conf.json`). Sign-then-notarize order matters:
notarize the signed **.app**, staple it, *then* build the DMG around the
already-stapled app (a DMG built before stapling needs its own separate
notarization pass, since Gatekeeper checks the outer disk image too).

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
5. `packaging/sign-and-notarize.sh` with the release identity.
6. Build + sign the DMG (once step 5 above in "The five steps" is
   implemented).
7. Smoke-test on a **second Mac** (or fresh VM) per the Phase 4 plan's
   verification criteria: FDA prompt names PersonalDB.app, `spctl -a -vv`
   and `codesign --verify --deep --strict` pass, and — the real test of
   signing stability — updating from a previous signed build preserves the
   existing FDA grant.
