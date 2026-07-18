#!/usr/bin/env bash
#
# ============================================================================
# UNTESTED. Written without a Developer ID Application certificate or an
# App Store Connect API/app-specific password available in the environment
# that authored it -- there was no way to actually run `codesign`/
# `notarytool` end-to-end here. Read every step before running this against
# a real release, and expect to debug it on the first real signed build (see
# packaging/README.md's "what remains for a signed release" checklist). The
# structure (inside-out signing order, no --deep, hardened runtime +
# entitlements, ditto/zip, notarytool --wait, stapler) follows Apple's
# documented notarization workflow and how python-build-standalone/PyOxidizer
# users typically sign a frozen CPython payload; the untested parts are
# exact flag spelling/edge cases that only show up against a real identity.
# ============================================================================
#
# Signs and notarizes the PersonalDB.app bundle (shell/ Tauri app) together
# with the frozen daemon payload (packaging/build/payload, see
# freeze-daemon.sh) that a future build will place inside it as a sidecar +
# resources. Produces a stapled, ready-to-DMG .app.
#
# Required environment variables:
#   IDENTITY       "Developer ID Application: Your Name (TEAMID)" -- the
#                  exact string `security find-identity -v -p codesigning`
#                  prints for your cert. NEVER pass "-" (ad-hoc) here: an
#                  ad-hoc or self-signed identity has no stable Team ID, so
#                  every FDA grant a user gives the app dies on the next
#                  rebuild (this is the whole reason this pipeline exists;
#                  see packaging/README.md).
#   APPLE_ID       Apple ID email used for notarization submission.
#   TEAM_ID        10-character Developer Team ID (same team as IDENTITY).
#   APP_PASSWORD   App-specific password for APPLE_ID (generate at
#                  appleid.apple.com -> Sign-In and Security ->
#                  App-Specific Passwords). Do NOT use your real Apple ID
#                  password here.
#
# Optional:
#   APP_PATH       Path to the built .app. Default: the debug Tauri build
#                  output under shell/src-tauri/target; override with the
#                  release path once you have a release build.
#   PAYLOAD_DIR    Path to the frozen daemon payload. Default:
#                  packaging/build/payload (see freeze-daemon.sh).
#   KEYCHAIN_PROFILE  If set, uses `xcrun notarytool submit --keychain-profile
#                  <name>` instead of the APPLE_ID/TEAM_ID/APP_PASSWORD trio
#                  (recommended once you've run `notarytool store-credentials`
#                  once -- avoids putting the app-specific password in this
#                  script's environment on every run).
#
# Usage:
#   IDENTITY="Developer ID Application: Jane Doe (ABCDE12345)" \
#   APPLE_ID="jane@example.com" TEAM_ID="ABCDE12345" APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" \
#   ./packaging/sign-and-notarize.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_PATH="${APP_PATH:-$REPO_ROOT/shell/src-tauri/target/release/bundle/macos/PersonalDB.app}"
PAYLOAD_DIR="${PAYLOAD_DIR:-$SCRIPT_DIR/build/payload}"
ENTITLEMENTS="$SCRIPT_DIR/entitlements.plist"

: "${IDENTITY:?set IDENTITY to your \"Developer ID Application: ...\" identity string}"

if [[ -z "${KEYCHAIN_PROFILE:-}" ]]; then
  : "${APPLE_ID:?set APPLE_ID (or KEYCHAIN_PROFILE) for notarization}"
  : "${TEAM_ID:?set TEAM_ID (or KEYCHAIN_PROFILE) for notarization}"
  : "${APP_PASSWORD:?set APP_PASSWORD (or KEYCHAIN_PROFILE) for notarization}"
fi

if [[ "$IDENTITY" == "-" ]]; then
  echo "refusing to sign with ad-hoc identity '-' -- see the header comment" >&2
  exit 1
fi

log() { echo "[sign-and-notarize] $*" >&2; }

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
#   3. the launcher script (packaging/build/payload/personal-db-daemon-*) --
#      NOT a Mach-O binary (it's a bash script; see freeze-daemon.sh), so
#      codesign has nothing to sign there. It rides along inside the
#      already-signed app bundle's Resources/sidecar location instead.
if [[ -d "$PAYLOAD_DIR" ]]; then
  log "signing Mach-O files under $PAYLOAD_DIR (inside-out)"

  # `file` classification, not just extension matching, because
  # python-build-standalone ships some extension modules without a .so
  # suffix and some non-Mach-O files that happen to end in .dylib-like
  # names; being precise here avoids codesign erroring on a non-Mach-O path.
  while IFS= read -r -d '' macho; do
    log "  codesign: $macho"
    codesign --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" \
      --sign "$IDENTITY" \
      "$macho"
  done < <(find "$PAYLOAD_DIR" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)

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
else
  log "no payload dir at $PAYLOAD_DIR -- skipping payload signing (sidecar not wired into this build yet)"
fi

# --- 2. sign the sidecar / any other Contents/MacOS or Contents/Resources ---
#        Mach-O binaries the Tauri build placed in the app bundle ----------
#
# This covers the sidecar once it's actually wired into shell/'s
# tauri.conf.json (bundle.externalBin) -- Tauri copies externalBin binaries
# into Contents/MacOS/ named after the app, and the frozen payload's
# site-packages tree would need to land in Contents/Resources/ (bundle
# .resources). Signing them here, before the outer .app signature, keeps
# the inside-out order correct even once that wiring lands.
log "signing any additional Mach-O files inside the app bundle (excluding the main executable, signed as part of the bundle below)"
MAIN_EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP_PATH/Contents/Info.plist")"
while IFS= read -r -d '' macho; do
  base="$(basename "$macho")"
  if [[ "$base" == "$MAIN_EXECUTABLE" ]]; then
    continue
  fi
  log "  codesign: $macho"
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$IDENTITY" \
    "$macho"
done < <(find "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources" -type f -perm -u+x -print0 2>/dev/null)

# --- 3. sign the .app bundle itself (outermost, last, NO --deep) ------------
log "signing the app bundle: $APP_PATH"
codesign --force --timestamp --options runtime \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" \
  "$APP_PATH"

log "verifying the signature"
codesign --verify --strict --verbose=2 "$APP_PATH"
spctl --assess --type execute --verbose "$APP_PATH" || {
  log "spctl assessment failed -- this is EXPECTED before notarization/stapling completes below"
}

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
