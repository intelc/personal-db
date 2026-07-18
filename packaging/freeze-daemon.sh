#!/usr/bin/env bash
# Freezes the personal_db daemon into a relocatable, self-contained payload:
#
#   packaging/build/payload/
#     python/                          python-build-standalone CPython, "install_only"
#     python/lib/python3.11/site-packages/personal_db/...   (+ deps, incl. finance/xhs extras)
#     personal-db-daemon-<TARGET_TRIPLE>   thin launcher, execs the embedded python
#
# This is NOT wired into shell/ as a Tauri sidecar yet (that's future work —
# see packaging/README.md). This script's job is just to produce the payload
# and prove it runs the daemon standalone (see VERIFY at the bottom / the
# `--verify` flag).
#
# Why python-build-standalone + uv, not a system Python: the whole point of
# freezing is that the shipped app must not depend on whatever Python happens
# to be on the end user's PATH (version, missing deps, a homebrew upgrade
# silently breaking things) — see packaging/README.md's TCC/FDA section for
# why a *stable* interpreter identity independent of brew/system Python
# matters even more than usual here (FDA grants die when the signed
# identity's binary changes).
#
# Usage:
#   ./packaging/freeze-daemon.sh                 # download + build + verify
#   ./packaging/freeze-daemon.sh --skip-verify    # build only, don't run the health check
#   ./packaging/freeze-daemon.sh --clean          # wipe packaging/build/ first
#
#   TARGET_TRIPLE=aarch64-apple-darwin ./packaging/freeze-daemon.sh   # (default; parameterized for
#                                                                       future Intel/Linux targets)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
DOWNLOAD_DIR="$BUILD_DIR/downloads"
PAYLOAD_DIR="$BUILD_DIR/payload"

# --- Configuration -----------------------------------------------------------

# The repo pins Python 3.11 (.python-version); python-build-standalone's
# release tag and the exact patch version are independent knobs (a PBS
# "release" bundles a fixed set of CPython patch versions), so both are
# pinned here explicitly rather than following "latest".
PBS_RELEASE="${PBS_RELEASE:-20260623}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11.15}"
TARGET_TRIPLE="${TARGET_TRIPLE:-aarch64-apple-darwin}"
DAEMON_PORT="${DAEMON_PORT:-8877}"

# python-build-standalone publishes a SHA256SUMS file per release
# (https://github.com/astral-sh/python-build-standalone/releases/download/<release>/SHA256SUMS).
# Pinning the checksum here (rather than fetching SHA256SUMS at build time
# and trusting it blindly) means a compromised release *or* a compromised
# download mirror both get caught — this is the actual pin, not a
# convenience cache. Add a new TARGET_TRIPLE by adding a case arm below,
# with its checksum from that file for the PBS_RELEASE you're pinning.
#
# Deliberately a `case`, not a bash 4 associative array: macOS ships bash
# 3.2 as `/bin/bash` (GPLv3 licensing, permanently frozen there) with no
# `declare -A` support, and this script shouldn't require the caller to
# have brew-installed a newer bash just to freeze a daemon.
pbs_sha256_for() {
  case "$1" in
    aarch64-apple-darwin) echo "d2324bfd1a7b9fc44ccd884c3a2505bcab6691dbfd4f8270e10c50aaa4e19506" ;;
    *) echo "" ;;
  esac
}

ASSET_NAME="cpython-${PYTHON_VERSION}+${PBS_RELEASE}-${TARGET_TRIPLE}-install_only.tar.gz"
ASSET_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${ASSET_NAME}"

CLEAN=0
DO_VERIFY=1
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=1 ;;
    --skip-verify) DO_VERIFY=0 ;;
    *) echo "unknown argument: $arg" >&2; exit 1 ;;
  esac
done

log() { echo "[freeze-daemon] $*" >&2; }

# --- 0. sanity ----------------------------------------------------------------

EXPECTED_SHA256="$(pbs_sha256_for "$TARGET_TRIPLE")"
if [[ -z "$EXPECTED_SHA256" ]]; then
  echo "no pinned checksum for TARGET_TRIPLE=$TARGET_TRIPLE (only aarch64-apple-darwin is" >&2
  echo "pinned right now). Add a case arm to pbs_sha256_for() in this script first —" >&2
  echo "see https://github.com/astral-sh/python-build-standalone/releases/tag/$PBS_RELEASE" >&2
  exit 1
fi

command -v uv >/dev/null 2>&1 || { echo "uv not found on PATH (https://astral.sh/uv)" >&2; exit 1; }

if [[ "$CLEAN" == "1" ]]; then
  log "cleaning $BUILD_DIR"
  rm -rf "$BUILD_DIR"
fi

mkdir -p "$DOWNLOAD_DIR" "$PAYLOAD_DIR"

# --- 1. download + verify the standalone CPython ------------------------------

TARBALL="$DOWNLOAD_DIR/$ASSET_NAME"

if [[ -f "$TARBALL" ]]; then
  log "reusing cached $TARBALL"
else
  log "downloading $ASSET_URL"
  curl -fL --retry 3 -o "$TARBALL.partial" "$ASSET_URL"
  mv "$TARBALL.partial" "$TARBALL"
fi

log "verifying checksum"
ACTUAL_SHA256="$(shasum -a 256 "$TARBALL" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "checksum mismatch for $ASSET_NAME" >&2
  echo "  expected: $EXPECTED_SHA256" >&2
  echo "  actual:   $ACTUAL_SHA256" >&2
  rm -f "$TARBALL"
  exit 1
fi
log "checksum OK ($ACTUAL_SHA256)"

# --- 2. extract into the payload dir -------------------------------------------

# python-build-standalone's "install_only" tarballs contain a single
# top-level `python/` directory, which is exactly the relocatable layout we
# want the launcher (step 4) to find its interpreter next to.
if [[ -x "$PAYLOAD_DIR/python/bin/python3" ]]; then
  log "reusing existing extracted interpreter at $PAYLOAD_DIR/python"
else
  log "extracting $ASSET_NAME into $PAYLOAD_DIR"
  rm -rf "$PAYLOAD_DIR/python"
  tar -xzf "$TARBALL" -C "$PAYLOAD_DIR"
fi

EMBEDDED_PYTHON="$PAYLOAD_DIR/python/bin/python3"
"$EMBEDDED_PYTHON" --version

# --- 3. build the wheel + install it (with extras) into the embedded python ---

log "building the personal_db wheel with uv"
rm -rf "$BUILD_DIR/dist"
uv build --wheel "$REPO_ROOT" --out-dir "$BUILD_DIR/dist"
WHEEL="$(ls "$BUILD_DIR"/dist/personal_db-*.whl | head -1)"
log "built $WHEEL"

# Product default is "batteries included": the frozen daemon ships with the
# finance app's LLM agent harness (openai-agents) and the xhs/xhs_saved
# trackers' Chrome-cookie decryption (cryptography) preinstalled, rather than
# making the end user pip-install extras into a frozen, no-pip-on-PATH
# runtime after the fact. This does make the payload noticeably bigger (see
# the size reported by --verify) — that's the deliberate tradeoff.
log "installing personal_db[finance,xhs] into the embedded interpreter"
uv pip install --python "$EMBEDDED_PYTHON" "${WHEEL}[finance,xhs]"

# --- 4. the launcher ------------------------------------------------------------

# Tauri's `externalBin` sidecar convention expects a binary literally named
# `<name>-<target-triple>` (it appends the triple itself when resolving
# which platform binary to bundle/run) — so that's the name here, even
# though wiring this into shell/src-tauri/tauri.conf.json's `bundle.externalBin`
# + `bundle.resources` (for the sibling `python/` tree the launcher needs) is
# future work, not done by this milestone.
#
# `python -m personal_db` is exactly the CLI entry point
# (personal_db.cli.main:app, wired via src/personal_db/__main__.py) — no
# need for the `-c "from personal_db.cli.main import app; app()"` fallback
# the plan anticipated; `-m personal_db` just works because the package
# already ships a `__main__.py`.
LAUNCHER="$PAYLOAD_DIR/personal-db-daemon-${TARGET_TRIPLE}"
cat > "$LAUNCHER" <<'LAUNCHER_EOF'
#!/usr/bin/env bash
# Relocatable launcher: looks for the embedded `python/` interpreter in
# either of the two layouts this launcher actually ships in:
#   1. sibling directory -- packaging/build/payload/ (this script's own
#      layout, and how freeze-daemon.sh's own --verify step runs it).
#   2. shell/src-tauri/tauri.conf.json's `bundle.externalBin` copies this
#      launcher into Contents/MacOS/ (the Tauri sidecar convention) while
#      `bundle.resources` copies the `python/` tree into Contents/Resources/
#      -- siblings under Contents/, but not siblings of each other, so the
#      lookup falls back to `../Resources/python` relative to this script.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$DIR/python/bin/python3" ]]; then
  PYTHON_BIN="$DIR/python/bin/python3"
elif [[ -x "$DIR/../Resources/python/bin/python3" ]]; then
  PYTHON_BIN="$(cd "$DIR/../Resources/python/bin" && pwd)/python3"
else
  echo "personal-db-daemon launcher: no embedded python found (looked in" >&2
  echo "  $DIR/python and $DIR/../Resources/python)" >&2
  exit 1
fi
exec "$PYTHON_BIN" -m personal_db "$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
log "launcher: $LAUNCHER"

PAYLOAD_SIZE="$(du -sh "$PAYLOAD_DIR" | awk '{print $1}')"
log "payload size: $PAYLOAD_SIZE ($PAYLOAD_DIR)"

# --- 5. verify: run the frozen daemon on a scratch root, curl health ----------

if [[ "$DO_VERIFY" == "1" ]]; then
  SCRATCH_ROOT="$(mktemp -d)"
  log "verify: running frozen daemon on scratch root $SCRATCH_ROOT (port $DAEMON_PORT)"
  PERSONAL_DB_ROOT="$SCRATCH_ROOT" "$LAUNCHER" dev daemon run --port "$DAEMON_PORT" &
  DAEMON_PID=$!
  cleanup() {
    kill "$DAEMON_PID" >/dev/null 2>&1 || true
    wait "$DAEMON_PID" 2>/dev/null || true
    rm -rf "$SCRATCH_ROOT"
  }
  trap cleanup EXIT

  READY=0
  for _ in $(seq 1 30); do
    if curl -fs "http://127.0.0.1:${DAEMON_PORT}/api/v1/health" >/tmp/freeze-daemon-health.json 2>/dev/null; then
      READY=1
      break
    fi
    sleep 0.5
  done

  if [[ "$READY" != "1" ]]; then
    echo "frozen daemon never became healthy on port $DAEMON_PORT" >&2
    exit 1
  fi

  log "health check OK:"
  cat /tmp/freeze-daemon-health.json >&2
  echo >&2
  rm -f /tmp/freeze-daemon-health.json
fi

log "done. payload: $PAYLOAD_DIR ($PAYLOAD_SIZE)"
