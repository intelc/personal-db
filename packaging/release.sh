#!/usr/bin/env bash
# One-command release: version-sync -> freeze -> build (+updater artifacts)
# -> deep-sign + notarize -> DMG -> latest.json -> draft GitHub release.
#
# Run this YOURSELF. The default mode prompts in a real terminal; optional
# --password-from-keychain mode is non-interactive (see the password note
# below). It ends with a *draft* release: nothing is public until you click
# Publish (or `gh release edit vX.Y.Z --draft=false`).
#
# Usage:
#   ./packaging/release.sh --notes "Fixed the foo, added the bar"
#   ./packaging/release.sh --notes-file RELEASE_NOTES.md
#   ./packaging/release.sh --password-from-keychain --notes "Fixed the foo"
#   ./packaging/release.sh --dry-run
#
# Flags:
#   --notes STR / --notes-file PATH   Release notes ("What's new"). Required
#                                     unless --dry-run. Shown verbatim in the
#                                     in-app update prompt (latest.json
#                                     `notes`) and as the GitHub release body.
#   --dry-run                         Validate the plumbing only: runs version
#                                     sync, the daemon freeze, and a real
#                                     `tauri build` -- then stops. No signing
#                                     key, no notarization, no gh calls, and
#                                     the --config overlay passed to the build
#                                     deliberately does NOT enable
#                                     bundle.createUpdaterArtifacts, so the
#                                     build runs fully unattended (enabling it
#                                     would make the Tauri CLI need the
#                                     updater signing key + its password).
#                                     Dry-run therefore does NOT produce
#                                     updater artifacts -- it proves the
#                                     pipeline runs, not the signatures. It
#                                     also tolerates a dirty git tree (warns
#                                     instead of aborting).
#   --password-from-keychain          Read the updater-key password from the
#                                     macOS login Keychain. Set it up once with
#                                     packaging/setup-updater-keychain.sh.
#                                     This mode can run without a TTY; the
#                                     password is scoped only to each Tauri
#                                     signing child process.
#
# Environment (all optional -- sane defaults):
#   KEYCHAIN_PROFILE            notarytool keychain profile
#                               (default: personal-db-notary)
#   TAURI_SIGNING_PRIVATE_KEY   updater minisign key path
#                               (default: ~/.tauri/personal-db-updater.key)
#   IDENTITY                    Developer ID Application identity for
#                               sign-and-notarize.sh / build-dmg.sh
#                               (default: the identity in tauri.conf.json)
#   PERSONAL_DB_UPDATER_KEYCHAIN_SERVICE / _ACCOUNT / _SECURITY_BIN
#                               Keychain item identity and security binary.
#                               Defaults are for the Personal DB release;
#                               overrides exist for controlled testing.
#
# THE UPDATER-KEY PASSWORD IS NEVER EXPORTED BY THIS SCRIPT. In optional
# --password-from-keychain mode it is injected only into each Tauri signer
# child process, never into this release shell or its other subprocesses.
# Verified behavior of the Tauri v2 CLI (crates/tauri-cli/src/bundle.rs):
# when TAURI_SIGNING_PRIVATE_KEY is set and TAURI_SIGNING_PRIVATE_KEY_PASSWORD
# is NOT, the CLI itself prompts interactively for the key password
# ("Decrypting updater signing key, expect a prompt for password") -- UNLESS
# it thinks it's in CI (the `CI` env var), in which case it silently assumes
# an empty password and fails against our protected key. So the build step
# below runs with CI explicitly unset and requires a real TTY.
#
# Updater-archive signing: the archive `tauri build` produces predates
# sign-and-notarize's deep re-sign of the frozen python payload, so step 4b
# discards it, re-tars the stapled .app, and re-signs the tarball with
# `tauri signer sign` (a second password prompt for the same updater key).
# Without this, updater-delivered copies fail spctl with "no usable
# signature" -- observed live on the v0.1.1 rollout.
#
# Sidecar signature (v0.1.2 rollout bug, separate from the above): the
# sidecar at Contents/MacOS/personal-db-daemon is now a copy of the frozen
# python3 Mach-O itself, not a bash launcher -- a script's signature is a
# detached xattr that Tauri's *client-side* updater extraction drops (this
# script's own `tar -czf` above preserves xattrs fine; the drop happens
# later, in the installed app's Rust updater unpacking this archive), so a
# script sidecar verified on this machine but failed `codesign -vv --deep`
# on every updater-delivered install. See packaging/freeze-daemon.sh step 4
# and shell/src-tauri/src/daemon.rs::try_start_sidecar for the fix.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { echo "[release] $*" >&2; }
die() { echo "[release] error: $*" >&2; exit 1; }

# Run a command whose updater-key password prompt reads /dev/tty, retrying up
# to 3 attempts when the failure is a mistyped password (marker on stderr:
# "incorrect updater private key password"). A typo at the step-4b prompt
# otherwise kills the whole run -- observed on the v0.1.4 rollout, costing a
# full re-build + notarization round-trip. Capturing stderr is safe here
# because the Tauri CLI prompts via /dev/tty, not stdin/stderr (verified: with
# no TTY it fails with "Device not configured" instead of reading stdin).
retry_key_password() {
  local label="$1"; shift
  local attempt err rc
  for attempt in 1 2 3; do
    err="$(mktemp "${TMPDIR:-/tmp}/release-pw-err.XXXXXX")"
    set +e
    "$@" 2> >(tee "$err" >&2)
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then rm -f "$err"; return 0; fi
    sleep 1  # let the tee process substitution flush before reading (bash 3.2: wait won't cover it)
    if grep -q "incorrect updater private key password" "$err"; then
      rm -f "$err"
      if [[ "$attempt" -lt 3 ]]; then
        log "$label: wrong updater-key password (attempt $attempt/3) -- try again"
        continue
      fi
      die "$label: wrong updater-key password on all 3 attempts"
    fi
    rm -f "$err"
    die "$label failed (exit $rc)"
  done
}

keychain_password_preflight() {
  local password
  if ! password="$("$UPDATER_KEYCHAIN_SECURITY_BIN" find-generic-password \
    -s "$UPDATER_KEYCHAIN_SERVICE" -a "$UPDATER_KEYCHAIN_ACCOUNT" -w 2>/dev/null)"; then
    die "updater password is missing from the Keychain; run $SCRIPT_DIR/setup-updater-keychain.sh"
  fi
  if [[ -z "$password" ]]; then
    unset password
    die "updater password in the Keychain is empty; rerun $SCRIPT_DIR/setup-updater-keychain.sh"
  fi
  unset password
}

# Read the password immediately before one signer invocation. It is a local
# shell value and the temporary environment assignment reaches only "$@" and
# descendants, not the release shell or later packaging commands.
run_with_keychain_password() {
  local label="$1"; shift
  local password err rc
  if ! password="$("$UPDATER_KEYCHAIN_SECURITY_BIN" find-generic-password \
    -s "$UPDATER_KEYCHAIN_SERVICE" -a "$UPDATER_KEYCHAIN_ACCOUNT" -w 2>/dev/null)"; then
    die "updater password is missing from the Keychain; run $SCRIPT_DIR/setup-updater-keychain.sh"
  fi
  if [[ -z "$password" ]]; then
    unset password
    die "updater password in the Keychain is empty; rerun $SCRIPT_DIR/setup-updater-keychain.sh"
  fi
  err="$(mktemp "${TMPDIR:-/tmp}/release-keychain-pw-err.XXXXXX")"
  set +e
  TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$password" "$@" 2> >(tee "$err" >&2)
  rc=$?
  set -e
  unset password
  if [[ "$rc" -eq 0 ]]; then
    rm -f "$err"
    return 0
  fi
  # Tauri's protected-key error is unambiguous. A retry would retrieve the
  # same stored value, so direct the releaser to update it instead.
  sleep 1  # let the tee process substitution flush before reading (bash 3.2)
  if grep -q "incorrect updater private key password" "$err"; then
    rm -f "$err"
    die "$label: stored updater-key password was rejected; rerun $SCRIPT_DIR/setup-updater-keychain.sh"
  fi
  rm -f "$err"
  die "$label failed (exit $rc)"
}

# --- flags -------------------------------------------------------------------

DRY_RUN=0
PASSWORD_FROM_KEYCHAIN=0
NOTES=""
NOTES_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --password-from-keychain) PASSWORD_FROM_KEYCHAIN=1; shift ;;
    --notes) NOTES="${2:?--notes needs a value}"; shift 2 ;;
    --notes-file) NOTES_FILE="${2:?--notes-file needs a path}"; shift 2 ;;
    -h|--help) sed -n '2,64p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

if [[ -n "$NOTES_FILE" ]]; then
  [[ -f "$NOTES_FILE" ]] || die "notes file not found: $NOTES_FILE"
  NOTES="$(cat "$NOTES_FILE")"
fi
if [[ "$DRY_RUN" == "0" && -z "$NOTES" ]]; then
  die "release notes are required: pass --notes \"...\" or --notes-file <path>"
fi

# --- 0. preflight ------------------------------------------------------------

KEYCHAIN_PROFILE="${KEYCHAIN_PROFILE:-personal-db-notary}"
TAURI_SIGNING_PRIVATE_KEY="${TAURI_SIGNING_PRIVATE_KEY:-$HOME/.tauri/personal-db-updater.key}"
IDENTITY="${IDENTITY:-Developer ID Application: Yiheng Chen (T78LM3Z7A5)}"
UPDATER_KEYCHAIN_SECURITY_BIN="${PERSONAL_DB_UPDATER_KEYCHAIN_SECURITY_BIN:-/usr/bin/security}"
UPDATER_KEYCHAIN_SERVICE="${PERSONAL_DB_UPDATER_KEYCHAIN_SERVICE:-com.personaldb.updater-signing}"
UPDATER_KEYCHAIN_ACCOUNT="${PERSONAL_DB_UPDATER_KEYCHAIN_ACCOUNT:-updater-key-password}"
export KEYCHAIN_PROFILE IDENTITY

# -uno: untracked files (local .claude/ config, scratch dirs) don't taint a
# release -- only modifications to tracked files do.
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain -uno)" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    log "WARNING: git tree is not clean (tolerated in --dry-run)"
  else
    die "git tree is not clean -- commit or stash before cutting a release"
  fi
fi

if [[ "$DRY_RUN" == "0" ]]; then
  [[ -f "$TAURI_SIGNING_PRIVATE_KEY" ]] \
    || die "updater signing key not found: $TAURI_SIGNING_PRIVATE_KEY"
  export TAURI_SIGNING_PRIVATE_KEY
  if [[ "$PASSWORD_FROM_KEYCHAIN" == "1" ]]; then
    [[ -x "$UPDATER_KEYCHAIN_SECURITY_BIN" ]] \
      || die "security binary is not executable: $UPDATER_KEYCHAIN_SECURITY_BIN"
    keychain_password_preflight
  else
    # The Tauri CLI's interactive key-password prompt (see header) needs a TTY.
    [[ -t 0 ]] || die "stdin is not a TTY -- run this in a real terminal so the \
Tauri CLI can prompt for the updater key password"
  fi
  command -v gh >/dev/null 2>&1 || die "gh CLI not found"
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated (run: gh auth login)"
fi

VERSION="$(PYPROJECT="$REPO_ROOT/pyproject.toml" "$REPO_ROOT/.venv/bin/python" - <<'PY'
import os, tomllib
print(tomllib.load(open(os.environ["PYPROJECT"], "rb"))["project"]["version"])
PY
)"
TAG="v$VERSION"
log "releasing $TAG (dry-run: $DRY_RUN)"

if [[ "$DRY_RUN" == "0" ]] && gh release view "$TAG" >/dev/null 2>&1; then
  die "release $TAG already exists on GitHub -- bump pyproject.toml first"
fi

# --- 1. version sync ---------------------------------------------------------

log "step 1: sync versions from pyproject.toml"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/sync-version.py"

# --- 2. freeze the daemon ----------------------------------------------------

log "step 2: freeze the daemon payload"
"$SCRIPT_DIR/freeze-daemon.sh"

# --- 3. tauri build (+ updater artifacts unless --dry-run) -------------------

# The Tauri CLI shells out to a bare `cargo`, which isn't on PATH in shells
# that only have rustup's shims (/opt/homebrew/bin/rustup). Resolve it once.
if ! command -v cargo >/dev/null 2>&1; then
  if command -v rustup >/dev/null 2>&1 && CARGO_BIN="$(rustup which cargo 2>/dev/null)"; then
    export PATH="$(dirname "$CARGO_BIN"):$PATH"
    log "cargo not on PATH -- using $(dirname "$CARGO_BIN") via rustup"
  else
    die "cargo not found -- install rust (rustup) or add cargo to PATH"
  fi
fi

if [[ ! -d "$REPO_ROOT/shell/node_modules" ]]; then
  log "shell/node_modules missing -- running npm install"
  (cd "$REPO_ROOT/shell" && npm install)
fi

if [[ "$DRY_RUN" == "1" ]]; then
  # Overlay that does NOT touch createUpdaterArtifacts: exercises the same
  # --config plumbing as a real release while keeping the build unattended
  # (no updater key, no password prompt).
  OVERLAY='{"bundle":{}}'
  log "step 3: tauri build (dry-run overlay: no updater artifacts)"
  (cd "$REPO_ROOT/shell" && npm run tauri build -- --config "$OVERLAY")
else
  OVERLAY='{"bundle":{"createUpdaterArtifacts":true}}'
  log "step 3: tauri build (updater artifacts ON -- the Tauri CLI will prompt"
  if [[ "$PASSWORD_FROM_KEYCHAIN" == "1" ]]; then
    log "        for the updater key password via the macOS Keychain)"
  else
    log "        for the updater key password; see the script header)"
  fi
  # `env -u CI`: with CI set, the CLI assumes an empty key password instead
  # of prompting, which fails against the protected key (see header).
  # retry_key_password: a mistyped password re-runs the build (cargo is
  # incremental, so the retry is cheap) instead of killing the release.
  build_with_updater_artifacts() {
    (cd "$REPO_ROOT/shell" && env -u CI npm run tauri build -- --config "$OVERLAY")
  }
  if [[ "$PASSWORD_FROM_KEYCHAIN" == "1" ]]; then
    run_with_keychain_password "step 3 tauri build" build_with_updater_artifacts
  else
    retry_key_password "step 3 tauri build" build_with_updater_artifacts
  fi
fi

BUNDLE_DIR="$REPO_ROOT/shell/src-tauri/target/release/bundle/macos"
APP_PATH="$BUNDLE_DIR/PersonalDB.app"
[[ -d "$APP_PATH" ]] || die "build finished but $APP_PATH does not exist"

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry run complete."
  log "  built (unsigned-for-updater, no updater artifacts): $APP_PATH"
  log "  skipped: sign-and-notarize, DMG, latest.json, gh release"
  exit 0
fi

UPDATER_ARCHIVE="$BUNDLE_DIR/PersonalDB.app.tar.gz"
UPDATER_SIG="$UPDATER_ARCHIVE.sig"
[[ -f "$UPDATER_ARCHIVE" ]] || die "updater archive not produced: $UPDATER_ARCHIVE"
[[ -f "$UPDATER_SIG" ]] || die "updater signature not produced: $UPDATER_SIG"

# --- 4. deep re-sign + notarize + staple ------------------------------------

log "step 4: sign-and-notarize (KEYCHAIN_PROFILE=$KEYCHAIN_PROFILE -- real submission)"
"$SCRIPT_DIR/sign-and-notarize.sh"

# --- 4b. re-create the updater archive from the DEEP-SIGNED app --------------
# `tauri build` tars the app before sign-and-notarize's deep re-sign, so the
# archive it produced carries an incomplete signature over the frozen python
# payload (updater-delivered copies then fail spctl with "no usable
# signature" -- observed live on the v0.1.1 rollout). Rebuild the tarball
# from the stapled app and re-sign it with the updater key (second password
# prompt, same key).

log "step 4b: re-tar updater archive from the signed app + tauri signer sign"
APP_PATH="$BUNDLE_DIR/PersonalDB.app"
[[ -d "$APP_PATH" ]] || die "signed app not found at $APP_PATH"
rm -f "$UPDATER_ARCHIVE" "$UPDATER_SIG"
# COPYFILE_DISABLE + --no-mac-metadata: write NO AppleDouble (._*) entries.
# bsdtar otherwise encodes xattrs as ._ files, which non-xattr-aware
# extractors (Tauri's updater) materialize as real files -- unsealed
# additions that Gatekeeper then rejects as "damaged" (observed on the
# v0.1.3 rollout). Every needed signature is embedded now; the archive
# must carry no metadata sidecars.
COPYFILE_DISABLE=1 tar --no-mac-metadata -czf "$UPDATER_ARCHIVE" -C "$(dirname "$APP_PATH")" "$(basename "$APP_PATH")"
sign_updater_archive() {
  (cd "$REPO_ROOT/shell" && env -u CI -u TAURI_SIGNING_PRIVATE_KEY npx tauri signer sign \
    --private-key-path "$TAURI_SIGNING_PRIVATE_KEY" "$UPDATER_ARCHIVE")
}
if [[ "$PASSWORD_FROM_KEYCHAIN" == "1" ]]; then
  run_with_keychain_password "step 4b signer sign" sign_updater_archive
else
  retry_key_password "step 4b signer sign" sign_updater_archive
fi
[[ -f "$UPDATER_SIG" ]] || die "tauri signer sign did not produce $UPDATER_SIG"

# --- 5. DMG ------------------------------------------------------------------

log "step 5: build the DMG"
DMG_PATH="$("$SCRIPT_DIR/build-dmg.sh")"
[[ -f "$DMG_PATH" ]] || die "build-dmg.sh did not report a DMG path"

# Stable-named copy of the same (signed, stapled) DMG, so
# releases/latest/download/PersonalDB_aarch64.dmg is an evergreen URL —
# the website's download button points at it.
DMG_ALIAS="$SCRIPT_DIR/build/PersonalDB_aarch64.dmg"
cp -f "$DMG_PATH" "$DMG_ALIAS"

# --- 6. latest.json ----------------------------------------------------------

log "step 6: assemble latest.json"
LATEST_JSON="$SCRIPT_DIR/build/latest.json"
ARCHIVE_NAME="$(basename "$UPDATER_ARCHIVE")"
NOTES="$NOTES" TAG="$TAG" SIG_FILE="$UPDATER_SIG" ARCHIVE_NAME="$ARCHIVE_NAME" \
  "$REPO_ROOT/.venv/bin/python" - > "$LATEST_JSON" <<'PY'
import json, os, datetime
sig = open(os.environ["SIG_FILE"]).read().strip()
tag = os.environ["TAG"]
print(json.dumps({
    "version": tag,
    "notes": os.environ["NOTES"],
    "pub_date": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    "platforms": {
        "darwin-aarch64": {
            "signature": sig,
            "url": f"https://github.com/intelc/personal-db/releases/download/{tag}/{os.environ['ARCHIVE_NAME']}",
        }
    },
}, indent=2))
PY
log "  wrote $LATEST_JSON"

# --- 7. draft GitHub release -------------------------------------------------

log "step 7: create draft release $TAG"
gh release create "$TAG" \
  --draft \
  --title "PersonalDB $TAG" \
  --notes "$NOTES" \
  "$DMG_PATH" "$DMG_ALIAS" "$UPDATER_ARCHIVE" "$UPDATER_SIG" "$LATEST_JSON"

RELEASE_URL="$(gh release view "$TAG" --json url --jq .url)"
log "done. Draft release: $RELEASE_URL"
log "Review it, then click Publish (or: gh release edit $TAG --draft=false)."
log "The in-app updater only sees it once published (latest.json is fetched"
log "from releases/latest/download/)."
