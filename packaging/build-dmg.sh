#!/usr/bin/env bash
# Builds a distributable DMG around the already-signed (and, for a public
# release, already-notarized-and-stapled) PersonalDB.app.
#
# Deliberately hdiutil, NOT Tauri's own "dmg" bundle target: `tauri build`
# with a dmg target re-bundles the .app from scratch (fresh copy of
# resources, its own single-pass signing), which would clobber the deep
# re-signing pass sign-and-notarize.sh does over the frozen payload
# (Contents/Resources/python/**). The pipeline order is:
#
#   1. packaging/freeze-daemon.sh            (payload)
#   2. cd shell && npm run tauri build       (bundle + Tauri's own signing)
#   3. packaging/sign-and-notarize.sh        (deep re-sign; + notarize/staple
#                                             when credentials are set)
#   4. packaging/build-dmg.sh                (this script)
#
# Sign-then-notarize-then-DMG order matters: notarize the signed .app,
# staple it, THEN build the DMG around the already-stapled app. A DMG built
# earlier needs its own separate notarization pass, since Gatekeeper checks
# the outer disk image too. Building an unnotarized "local" DMG (what a
# sign-only run of step 3 leads to) is fine for testing -- it just shows
# the same "Unnotarized Developer ID" spctl rejection as the bare .app.
#
# Optional environment variables:
#   APP_PATH   Path to the signed .app. Default: the release Tauri build
#              output (shell/src-tauri/target/release/bundle/macos).
#   OUT_DIR    Where to put the DMG. Default: packaging/build/.
#   IDENTITY   If set, the finished DMG itself is codesigned with it
#              (recommended for distribution; harmless to skip for a local
#              test build).
#   KEYCHAIN_PROFILE, or APPLE_ID + TEAM_ID + APP_PASSWORD
#              Same credential pair sign-and-notarize.sh takes. If present
#              (and IDENTITY is set -- the notary service only takes signed
#              submissions), the signed DMG is itself notarized and stapled,
#              so Gatekeeper accepts the download without a network check.
#              The .app inside is already stapled by sign-and-notarize.sh;
#              this covers the outer disk image, which Gatekeeper checks
#              separately. Omit (sign-only / local builds) to skip -- that
#              keeps the pre-2026-07-21 behavior.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_PATH="${APP_PATH:-$REPO_ROOT/shell/src-tauri/target/release/bundle/macos/PersonalDB.app}"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/build}"

log() { echo "[build-dmg] $*" >&2; }

[[ -d "$APP_PATH" ]] || { echo "app bundle not found: $APP_PATH" >&2; exit 1; }

# Version from the bundle itself (single source of truth once built), arch
# from the main executable -- gives PersonalDB_<version>_<arch>.dmg, the
# same naming Tauri's own dmg target would use.
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP_PATH/Contents/Info.plist")"
MAIN_EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP_PATH/Contents/Info.plist")"
ARCH="$(lipo -archs "$APP_PATH/Contents/MacOS/$MAIN_EXECUTABLE" | tr ' ' '-')"
case "$ARCH" in
  arm64) ARCH="aarch64" ;;
  x86_64) ARCH="x64" ;;
esac

DMG_NAME="PersonalDB_${VERSION}_${ARCH}.dmg"
DMG_PATH="$OUT_DIR/$DMG_NAME"
mkdir -p "$OUT_DIR"
rm -f "$DMG_PATH"

# Stage the DMG contents: the .app plus the customary /Applications symlink
# so the mounted image is a drag-to-install surface.
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
log "staging $APP_PATH"
# ditto preserves signatures/xattrs/symlinks exactly (cp -R can subtly
# damage a signed bundle's metadata).
ditto "$APP_PATH" "$STAGING/$(basename "$APP_PATH")"
ln -s /Applications "$STAGING/Applications"

log "building $DMG_PATH"
# UDZO = compressed, read-only -- the standard distribution format.
hdiutil create \
  -volname "PersonalDB" \
  -srcfolder "$STAGING" \
  -ov -format UDZO \
  "$DMG_PATH" >&2

if [[ -n "${IDENTITY:-}" ]]; then
  log "signing the DMG itself"
  codesign --force --timestamp --sign "$IDENTITY" "$DMG_PATH"
  codesign --verify --verbose=2 "$DMG_PATH"
fi

# Notarize + staple the DMG itself when credentials are available (same
# gating as sign-and-notarize.sh). Gatekeeper assesses the outer disk image
# separately from the .app inside it; a stapled DMG opens cleanly even
# offline. If the notary service rejects the submission there is no ticket,
# so `stapler staple` fails and set -e aborts the release -- the failure
# mode we want.
NOTARIZE_DMG=0
if [[ -n "${KEYCHAIN_PROFILE:-}" ]]; then
  NOTARIZE_DMG=1
elif [[ -n "${APPLE_ID:-}" || -n "${TEAM_ID:-}" || -n "${APP_PASSWORD:-}" ]]; then
  : "${APPLE_ID:?set APPLE_ID (or KEYCHAIN_PROFILE) for DMG notarization}"
  : "${TEAM_ID:?set TEAM_ID (or KEYCHAIN_PROFILE) for DMG notarization}"
  : "${APP_PASSWORD:?set APP_PASSWORD (or KEYCHAIN_PROFILE) for DMG notarization}"
  NOTARIZE_DMG=1
fi

if [[ "$NOTARIZE_DMG" == "1" ]]; then
  if [[ -z "${IDENTITY:-}" ]]; then
    # Unsigned submissions are rejected outright; treat this as a config
    # error rather than silently shipping an unstapled DMG from a run that
    # clearly intended a full release.
    echo "notarization credentials set but IDENTITY is not -- the notary service only accepts signed DMGs" >&2
    exit 1
  fi
  log "notarizing the DMG itself"
  if [[ -n "${KEYCHAIN_PROFILE:-}" ]]; then
    xcrun notarytool submit "$DMG_PATH" --keychain-profile "$KEYCHAIN_PROFILE" --wait >&2
  else
    xcrun notarytool submit "$DMG_PATH" \
      --apple-id "$APPLE_ID" \
      --team-id "$TEAM_ID" \
      --password "$APP_PASSWORD" \
      --wait >&2
  fi
  log "stapling the DMG"
  xcrun stapler staple "$DMG_PATH" >&2
  log "verifying stapled DMG"
  spctl -a -vv -t open --context context:primary-signature "$DMG_PATH" >&2
fi

SIZE="$(du -h "$DMG_PATH" | awk '{print $1}')"
log "done: $DMG_PATH ($SIZE)"
echo "$DMG_PATH"
