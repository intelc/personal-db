#!/usr/bin/env bash
#
# ============================================================================
# HARDENED / FIRST REAL RUN completed against an actual Developer ID
# Application certificate (see packaging/README.md's release checklist for
# the exact run). What changed from the original untested draft:
#
#   - entitlements.plist had a real bug: its header comment used " -- " as a
#     prose dash, which is illegal inside an XML comment (`--` may not
#     appear in `<!-- ... -->` content except immediately before `-->`).
#     `codesign --entitlements` parses with Apple's AMFIUnserializeXML,
#     which is strict about this (unlike, say, Python's plistlib, which
#     silently tolerates it) -- it failed with "syntax error near line 19"
#     on the very first real signing attempt. Fixed by rewording, not by
#     loosening anything.
#   - `shell/src-tauri/tauri.conf.json` now sets `bundle.macOS.signingIdentity`
#     + `entitlements` + `hardenedRuntime`, so `npm run tauri build` signs
#     the app bundle, its main executable, and the sidecar
#     (Contents/MacOS/personal-db-daemon) itself -- fast (~20s), because it
#     is a single top-level signature per file, NOT a deep walk of
#     Contents/Resources/python. Nested payload files
#     (Contents/Resources/python/**/*.{so,dylib}, the interpreter itself)
#     come out of that build still carrying python-build-standalone's own
#     adhoc/linker-signed signatures -- fine for local Gatekeeper/hardened
#     runtime checks (`codesign --verify --deep --strict` and
#     `spctl -a -vv -t exec` both pass/behave as expected pre-notarization;
#     `disable-library-validation` in entitlements.plist is what lets the
#     interpreter dlopen adhoc-signed extension modules at all), but Apple's
#     notary service is stricter: every piece of executable code in a
#     submitted bundle must carry a real Developer ID (or platform)
#     signature, not adhoc. That's what THIS script's step 1/2 exist to fix
#     before notarizing -- re-signing the nested payload (and the sidecar,
#     redundantly but harmlessly re-signing what Tauri already signed) with
#     the real identity, then re-signing the outer .app last (required
#     because step 1 changes nested file hashes, which invalidates the
#     outer bundle's Sealed Resources manifest that Tauri's build wrote).
#   - Notarization credentials are now optional, not required up front: if
#     none of KEYCHAIN_PROFILE / (APPLE_ID + TEAM_ID + APP_PASSWORD) are set,
#     the script signs (steps 1-3), verifies, and stops there with a clear
#     message -- it does NOT abort before doing any work like the original
#     draft did. Notarize + staple (steps 4-6) are the separate, explicitly
#     skippable tail end.
# ============================================================================
#
# Signs (and optionally notarizes) the PersonalDB.app bundle (shell/ Tauri
# app), including the frozen daemon payload wired in as a sidecar +
# resources (packaging/freeze-daemon.sh, shell/src-tauri/tauri.conf.json's
# bundle.externalBin/resources). Run after `npm run tauri build` in shell/.
#
# Required environment variable:
#   IDENTITY       "Developer ID Application: Your Name (TEAMID)" -- the
#                  exact string `security find-identity -v -p codesigning`
#                  prints for your cert. NEVER pass "-" (ad-hoc) here: an
#                  ad-hoc or self-signed identity has no stable Team ID, so
#                  every FDA grant a user gives the app dies on the next
#                  rebuild (this is the whole reason this pipeline exists;
#                  see packaging/README.md).
#
# Optional (signing only; no effect without these):
#   APP_PATH       Path to the built .app. Default: the release Tauri build
#                  output under shell/src-tauri/target/release/bundle/macos.
#   PAYLOAD_DIR    Path to freeze-daemon.sh's standalone payload (default:
#                  packaging/build/payload). Signing this is independent of
#                  what ships inside APP_PATH (Tauri already copied its own
#                  snapshot of it into the bundle by the time this script
#                  runs) -- it's here so `PERSONAL_DB_ROOT=... PAYLOAD_DIR/
#                  personal-db-daemon-* dev daemon run` also runs under a
#                  real-identity-signed interpreter when testing the frozen
#                  daemon standalone, outside the app bundle.
#
# Optional (enables notarize + staple; omit all three/four to sign-only):
#   KEYCHAIN_PROFILE  Preferred: a profile created once via
#                  `xcrun notarytool store-credentials <name>` (avoids ever
#                  putting an app-specific password in this script's
#                  environment). If set, the APPLE_ID/TEAM_ID/APP_PASSWORD
#                  trio below is not needed.
#   APPLE_ID       Apple ID email used for notarization submission.
#   TEAM_ID        10-character Developer Team ID (same team as IDENTITY).
#   APP_PASSWORD   App-specific password for APPLE_ID (generate at
#                  appleid.apple.com -> Sign-In and Security ->
#                  App-Specific Passwords). Do NOT use your real Apple ID
#                  password here.
#
# Usage (sign only -- what a CI build or a dry run should do):
#   IDENTITY="Developer ID Application: Jane Doe (ABCDE12345)" \
#   ./packaging/sign-and-notarize.sh
#
# Usage (sign + notarize + staple, once you have a keychain profile):
#   IDENTITY="Developer ID Application: Jane Doe (ABCDE12345)" \
#   KEYCHAIN_PROFILE="personal-db-notary" \
#   ./packaging/sign-and-notarize.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_PATH="${APP_PATH:-$REPO_ROOT/shell/src-tauri/target/release/bundle/macos/PersonalDB.app}"
PAYLOAD_DIR="${PAYLOAD_DIR:-$SCRIPT_DIR/build/payload}"
ENTITLEMENTS="$SCRIPT_DIR/entitlements.plist"

: "${IDENTITY:?set IDENTITY to your \"Developer ID Application: ...\" identity string}"

# Notarization credentials are optional -- their presence is what decides
# whether steps 4-6 (zip/notarize/staple) run at all. No hard require here:
# a bare `IDENTITY=... ./sign-and-notarize.sh` must sign + verify + DMG-ready
# the app and stop cleanly, not abort before doing any work.
NOTARIZE=0
if [[ -n "${KEYCHAIN_PROFILE:-}" ]]; then
  NOTARIZE=1
elif [[ -n "${APPLE_ID:-}" || -n "${TEAM_ID:-}" || -n "${APP_PASSWORD:-}" ]]; then
  : "${APPLE_ID:?set APPLE_ID (or KEYCHAIN_PROFILE) for notarization}"
  : "${TEAM_ID:?set TEAM_ID (or KEYCHAIN_PROFILE) for notarization}"
  : "${APP_PASSWORD:?set APP_PASSWORD (or KEYCHAIN_PROFILE) for notarization}"
  NOTARIZE=1
fi

if [[ "$IDENTITY" == "-" ]]; then
  echo "refusing to sign with ad-hoc identity '-' -- see the header comment" >&2
  exit 1
fi

log() { echo "[sign-and-notarize] $*" >&2; }

# Classifies by content (via `file`), not extension or permission bits.
# python-build-standalone ships plenty of .so/.dylib extension modules
# *without* the executable permission bit set (they're dlopen'd, never
# exec'd directly, so pip/build tooling doesn't bother setting +x) -- an
# earlier version of this script filtered candidates on `-perm -u+x` in
# step 2 and, verified against the actual payload, silently missed ~85% of
# it (26 of 172 .so/.dylib files under Contents/Resources/python had +x;
# the other 146 would have shipped still adhoc-signed).
is_macho() {
  case "$(file -b "$1" 2>/dev/null)" in
    Mach-O*) return 0 ;;
    *) return 1 ;;
  esac
}

[[ -d "$APP_PATH" ]] || { echo "app bundle not found: $APP_PATH" >&2; exit 1; }
[[ -f "$ENTITLEMENTS" ]] || { echo "entitlements not found: $ENTITLEMENTS" >&2; exit 1; }

# --- 1. sign the frozen payload's Mach-O files, inside-out ------------------
#
# "Inside-out" = leaf dependencies first, then the things that load them.
# codesign itself doesn't walk dependency graphs (that's what `--deep` does,
# and `--deep` is explicitly avoided here -- Apple's own guidance is that
# --deep papers over signing-order bugs and should not be used for
# production signing; get the order right instead):
#   1. every compiled extension module (*.so) and dylib the interpreter
#      might dlopen, found anywhere under the payload's site-packages
#   2. the python3 interpreter binary itself (which links against some of
#      the above at startup, e.g. libpython3.11.dylib)
#   3. the standalone sidecar copy (packaging/build/payload/personal-db-daemon-*)
#      -- since the "script sidecar loses its signature through the
#      updater" fix, this is a COPY of the same python3 Mach-O as step 2,
#      not a bash launcher (a script's signature is a detached xattr that
#      Tauri's updater extraction drops; a Mach-O's is embedded and
#      survives it -- see packaging/freeze-daemon.sh step 4's comment).
#      Signed explicitly here too so the standalone payload (used for
#      testing the frozen daemon outside the app bundle) is consistent with
#      what ships inside it. Step 2 below re-signs it again once it's
#      inside the app bundle at Contents/MacOS/personal-db-daemon
#      (bundle.externalBin's convention), same as it already got signed
#      once by `tauri build` itself -- redundant but harmless.
if [[ -d "$PAYLOAD_DIR" ]]; then
  log "signing Mach-O files under $PAYLOAD_DIR (inside-out)"

  # Candidates are anything named like a compiled extension/library; each
  # candidate is then confirmed with `is_macho` (content classification)
  # before signing, since some non-Mach-O files can happen to end in a
  # .dylib-like name, and this avoids codesign erroring on those.
  payload_signed=0
  while IFS= read -r -d '' candidate; do
    is_macho "$candidate" || continue
    codesign --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" \
      --sign "$IDENTITY" \
      "$candidate"
    payload_signed=$((payload_signed + 1))
    if (( payload_signed % 200 == 0 )); then
      log "  ...$payload_signed files signed so far"
    fi
  done < <(find "$PAYLOAD_DIR" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)
  log "  signed $payload_signed Mach-O files under $PAYLOAD_DIR"

  # The interpreter binary(ies) -- python-build-standalone's "install_only"
  # layout puts the real executable at python/bin/python3.<minor> with
  # python3/python as symlinks; sign the real file, not the symlinks
  # (codesign follows symlinks anyway, but being explicit avoids double-
  # signing confusion in the log).
  PYTHON_BIN="$(find "$PAYLOAD_DIR/python/bin" -maxdepth 1 -type f -perm -u+x -name 'python3.*' | head -1)"
  if [[ -n "$PYTHON_BIN" ]]; then
    log "  codesign (interpreter): $PYTHON_BIN"
    codesign --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" \
      --sign "$IDENTITY" \
      "$PYTHON_BIN"
  else
    log "  WARNING: couldn't find the python3.X interpreter binary under $PAYLOAD_DIR/python/bin"
  fi

  # The standalone sidecar copy (see the comment above step 1) -- a
  # separate file from $PYTHON_BIN above (freeze-daemon.sh `cp`s it), so it
  # needs its own explicit sign here.
  SIDECAR_BIN="$(find "$PAYLOAD_DIR" -maxdepth 1 -type f -perm -u+x -name 'personal-db-daemon-*' | head -1)"
  if [[ -n "$SIDECAR_BIN" ]]; then
    log "  codesign (sidecar copy): $SIDECAR_BIN"
    codesign --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" \
      --sign "$IDENTITY" \
      "$SIDECAR_BIN"
  else
    log "  WARNING: couldn't find the sidecar binary (personal-db-daemon-*) under $PAYLOAD_DIR"
  fi
else
  log "no payload dir at $PAYLOAD_DIR -- skipping payload signing (sidecar not wired into this build yet)"
fi

# --- 2. sign the sidecar / any other Contents/MacOS or Contents/Resources ---
#        files the Tauri build placed in the app bundle ---------------------
#
# tauri.conf.json's bundle.externalBin copies the launcher into
# Contents/MacOS/personal-db-daemon; bundle.resources copies the frozen
# python/ tree into Contents/Resources/python. `tauri build` (with
# bundle.macOS.signingIdentity set) already signs both of those plus the
# main executable and the outer .app -- but only as single top-level
# signatures, not a deep walk, so files under Contents/Resources/python/**
# (the interpreter, every extension module) are untouched by that pass and
# still carry python-build-standalone's own adhoc signatures. This loop
# re-signs everything executable it finds under Contents/MacOS and
# Contents/Resources with the real identity (redundant but harmless for the
# couple of files Tauri already signed), before the outer .app signature
# below, keeping the inside-out order correct.
log "signing all Mach-O files inside the app bundle (excluding the main executable, signed as part of the bundle below)"
MAIN_EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP_PATH/Contents/Info.plist")"
bundle_signed=0
bundle_scanned=0
while IFS= read -r -d '' candidate; do
  bundle_scanned=$((bundle_scanned + 1))
  base="$(basename "$candidate")"
  if [[ "$base" == "$MAIN_EXECUTABLE" ]]; then
    continue
  fi
  is_macho "$candidate" || continue
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$IDENTITY" \
    "$candidate"
  bundle_signed=$((bundle_signed + 1))
  if (( bundle_signed % 200 == 0 )); then
    log "  ...$bundle_signed Mach-O files signed so far ($bundle_scanned candidates scanned)"
  fi
done < <(find "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources" -type f -print0 2>/dev/null)
log "  signed $bundle_signed Mach-O files inside the app bundle ($bundle_scanned total files scanned)"

# --- 3. sign the .app bundle itself (outermost, last, NO --deep) ------------
log "signing the app bundle: $APP_PATH"
codesign --force --timestamp --options runtime \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" \
  "$APP_PATH"

log "verifying the signature"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
spctl --assess --type execute --verbose "$APP_PATH" || {
  log "spctl assessment rejected -- EXPECTED pre-notarization (source=Unnotarized Developer ID);"
  log "this is not a failure of this script"
}

if [[ "$NOTARIZE" != "1" ]]; then
  # Pull the team id out of IDENTITY ("Developer ID Application: Name (TEAMID)")
  # just to make the next-step hint below copy-pasteable without the user
  # having to go look it up again.
  TEAM_ID_HINT="$(sed -n 's/.*(\([A-Z0-9]*\))/\1/p' <<<"$IDENTITY")"
  TEAM_ID_HINT="${TEAM_ID_HINT:-<TEAMID>}"
  log "no notarization credentials set (KEYCHAIN_PROFILE or APPLE_ID+TEAM_ID+APP_PASSWORD) --"
  log "stopping here. The app is signed and verified; steps 4-6 (zip, notarize, staple)"
  log "are skipped. To notarize later:"
  log "  xcrun notarytool store-credentials personal-db-notary \\"
  log "    --apple-id <you@example.com> --team-id $TEAM_ID_HINT --password <app-specific password>"
  log "  KEYCHAIN_PROFILE=personal-db-notary IDENTITY=\"$IDENTITY\" ./packaging/sign-and-notarize.sh"
  log "next: build a DMG (see packaging/README.md) -- notarize the .app first if you want a"
  log "fully Gatekeeper-clean DMG; a DMG built around an unnotarized .app still shows the"
  log "same 'Unnotarized Developer ID' rejection until the .app inside it is notarized."
  exit 0
fi

# --- 4. zip for notarization -------------------------------------------------
ZIP_PATH="$SCRIPT_DIR/build/PersonalDB-for-notarization.zip"
mkdir -p "$(dirname "$ZIP_PATH")"
rm -f "$ZIP_PATH"
log "zipping for notarization: $ZIP_PATH"
# `ditto -c -k --keepParent`, not `zip`, is Apple's documented method --
# preserves resource forks/extended attributes that a raw `zip` can mangle.
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

# --- 5. submit for notarization and wait -------------------------------------
log "submitting to notarytool (this polls Apple's service and can take several minutes)"
if [[ -n "${KEYCHAIN_PROFILE:-}" ]]; then
  xcrun notarytool submit "$ZIP_PATH" --keychain-profile "$KEYCHAIN_PROFILE" --wait
else
  xcrun notarytool submit "$ZIP_PATH" \
    --apple-id "$APPLE_ID" \
    --team-id "$TEAM_ID" \
    --password "$APP_PASSWORD" \
    --wait
fi

# --- 6. staple the notarization ticket to the .app ---------------------------
log "stapling notarization ticket"
xcrun stapler staple "$APP_PATH"

log "final verification"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
spctl --assess --type execute --verbose "$APP_PATH"

log "done: $APP_PATH is signed, notarized, and stapled."
log "next: build a DMG (see packaging/README.md) for distribution."
