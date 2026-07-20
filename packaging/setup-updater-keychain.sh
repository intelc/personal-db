#!/usr/bin/env bash
# Store the protected Tauri updater-key password in the login keychain.
#
# The `security` tool prompts for the password because -w is deliberately its
# final argument. Do not add a password argument here: that would expose the
# secret in shell history and the process list.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./packaging/setup-updater-keychain.sh

Prompt once to store or update the Personal DB updater-key password in the
macOS login Keychain. The password is never accepted as an argument.
EOF
}

case "$#" in
  0) ;;
  1)
    case "$1" in
      -h|--help) usage; exit 0 ;;
      *) echo "[updater-keychain] error: unknown argument: $1" >&2; exit 1 ;;
    esac
    ;;
  *) echo "[updater-keychain] error: this script accepts no arguments" >&2; exit 1 ;;
esac

SECURITY_BIN="${PERSONAL_DB_UPDATER_KEYCHAIN_SECURITY_BIN:-/usr/bin/security}"
KEYCHAIN_SERVICE="${PERSONAL_DB_UPDATER_KEYCHAIN_SERVICE:-com.personaldb.updater-signing}"
KEYCHAIN_ACCOUNT="${PERSONAL_DB_UPDATER_KEYCHAIN_ACCOUNT:-updater-key-password}"

log() { echo "[updater-keychain] $*" >&2; }
die() { echo "[updater-keychain] error: $*" >&2; exit 1; }

[[ -x "$SECURITY_BIN" ]] || die "security binary is not executable: $SECURITY_BIN"

log "storing updater password in the login keychain (you will be prompted)"
# -U replaces an existing item. -T limits its Keychain ACL to Apple's
# security tool; keep -w last so it has no password value in argv.
"$SECURITY_BIN" add-generic-password -U \
  -s "$KEYCHAIN_SERVICE" \
  -a "$KEYCHAIN_ACCOUNT" \
  -T /usr/bin/security \
  -w

# Do not use -w here: this confirms the item exists without rendering the
# secret to stdout/stderr.
"$SECURITY_BIN" find-generic-password \
  -s "$KEYCHAIN_SERVICE" \
  -a "$KEYCHAIN_ACCOUNT" \
  >/dev/null 2>&1 \
  || die "Keychain item was not found after setup"

verify_nonempty_password() {
  local password
  # Command substitution keeps the returned secret out of terminal output.
  if ! password="$("$SECURITY_BIN" find-generic-password \
    -s "$KEYCHAIN_SERVICE" \
    -a "$KEYCHAIN_ACCOUNT" \
    -w 2>/dev/null)"; then
    die "could not read the Keychain item after setup"
  fi
  if [[ -z "$password" ]]; then
    # Avoid leaving a known-empty credential behind if security accepted EOF.
    "$SECURITY_BIN" delete-generic-password \
      -s "$KEYCHAIN_SERVICE" \
      -a "$KEYCHAIN_ACCOUNT" \
      >/dev/null 2>&1 || true
    unset password
    die "the entered updater password was empty; no credential was kept"
  fi
  unset password
}

verify_nonempty_password

log "updater password is stored for this macOS user"
