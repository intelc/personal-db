#!/usr/bin/env bash
#
# personal-db installer
# https://github.com/intelc/personal-db
#
# Usage (one-liner):
#   curl -LsSf https://raw.githubusercontent.com/intelc/personal-db/main/install.sh | bash
#
# Usage (with auto-launching setup wizard):
#   curl -LsSf https://raw.githubusercontent.com/intelc/personal-db/main/install.sh -o install.sh
#   bash install.sh
#
# Environment:
#   PERSONAL_DB_NO_SETUP=1   skip auto-launching the setup wizard

set -euo pipefail

REPO_URL="git+https://github.com/intelc/personal-db.git"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
fatal() { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Platform check — v0 is macOS only
case "$(uname)" in
    Darwin) ;;
    *) fatal "personal-db v0 supports macOS only. Detected: $(uname)" ;;
esac

# 2. Ensure uv is installed
if ! command -v uv >/dev/null 2>&1; then
    info "uv not found — installing now"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv's installer modifies your shell rc, but this subshell doesn't see
    # that yet. Add ~/.local/bin to PATH for the rest of this script.
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || fatal "uv install failed — not on PATH"
else
    info "uv already installed ($(uv --version))"
fi

# 3. Install personal-db (force ensures re-runs pull latest from main)
info "Installing personal-db from $REPO_URL"
uv tool install --force "$REPO_URL"

# 4. Make sure the personal-db shim is reachable
if ! command -v personal-db >/dev/null 2>&1; then
    export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v personal-db >/dev/null 2>&1; then
    warn "personal-db installed but not yet on PATH for this shell."
    warn 'Add this to your shell rc and reopen your terminal:'
    warn '    export PATH="$HOME/.local/bin:$PATH"'
    warn "Then run: personal-db setup"
    exit 0
fi

info "Installed: $(command -v personal-db)"

# 5. Decide whether to auto-launch the setup wizard
if [ "${PERSONAL_DB_NO_SETUP:-0}" = "1" ]; then
    info "PERSONAL_DB_NO_SETUP=1 — skipping setup."
    info "Run: personal-db setup"
    exit 0
fi

# stdin is a TTY when the script was downloaded and run directly (bash install.sh).
# Under `curl | bash`, stdin is the curl pipe — no TTY, so the wizard's prompts
# would fail. In that case, print the next-step message instead.
if [ -t 0 ]; then
    info "Starting setup wizard..."
    exec personal-db setup
else
    cat <<'EOF'

Install complete.

To finish setup, run:

    personal-db setup

This walks you through init + tracker configuration. You can pick a
browser-based wizard or a terminal-based one when prompted.

EOF
fi
