#!/usr/bin/env bash
# Kendra desktop launcher.
#
# This is the entry point used by the double-clickable Kendra.app bundle and by
# anyone starting Virtual Kendra from a Terminal. A double-clicked app gets no
# login shell, no profile, no PATH, no working directory, and no visible stdout,
# so this script establishes all four itself and reports failures with a dialog
# instead of dying silently.
#
#   scripts/kendra_desktop_launcher.sh [config/pc.yaml | config/webots.yaml]
#
# Environment:
#   KENDRA_CONFIG   Kendra profile (default config/pc.yaml)
#   KENDRA_GUI      set to 1 by Kendra.app; enables dialog reporting
#
# PLATFORM: Intel iMac (x86_64 macOS) ONLY, by design.
# Kendra's robot body has no desktop, no Electron, and no display server. The
# Raspberry Pi 5 runs the same Python runtime under the systemd units in
# systemd/ -- see docs/INSTALL_PI.md. Nothing in this script is required for,
# or ever runs on, the robot. Kendra's brain, voice, vision, reflex, and body
# services are all platform-portable; only this desktop shell is not.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/desktop-launcher.log"
mkdir -p "$LOG_DIR"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

fail() {
  local message="$1"
  log "LAUNCH FAILED: $message"
  if [ "${KENDRA_GUI:-0}" = "1" ] && command -v osascript >/dev/null 2>&1; then
    osascript -e "display dialog \"Kendra could not start.

${message//\"/\'}

Details: logs/desktop-launcher.log\" with title \"Kendra\" buttons {\"OK\"} default button \"OK\" with icon caution" >/dev/null 2>&1 || true
  fi
  exit 2
}

# A GUI launch inherits almost nothing, so build a known-good PATH.
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
if [ -x /usr/local/bin/brew ]; then
  if NODE22="$(/usr/local/bin/brew --prefix node@22 2>/dev/null)" && [ -d "$NODE22/bin" ]; then
    export PATH="$NODE22/bin:$PATH"
  fi
fi

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
  CHECK_ONLY=1
  shift
fi

CONFIG="${1:-${KENDRA_CONFIG:-config/pc.yaml}}"

log "=== Kendra desktop launch ==="
log "root=$ROOT config=$CONFIG node=$(command -v node || echo missing) arch=$(uname -m)"

# ---- Preflight -------------------------------------------------------------
[ "$(uname -s)" = "Darwin" ] || fail "This launcher targets macOS. Use scripts/bootstrap_dev.sh elsewhere."
[ -f "$CONFIG" ] || fail "Missing Kendra profile: $CONFIG"
[ -x "$ROOT/.venv/bin/python" ] || fail "Missing Python environment (.venv). Run scripts/bootstrap_intel_macos.sh."
command -v node >/dev/null 2>&1 || fail "Node.js is not installed. Run: brew install node@22"

if [ ! -d "$ROOT/dashboard/node_modules/electron" ] || [ ! -f "$ROOT/dashboard/dist/index.html" ]; then
  log "Desktop bundle is missing or incomplete; running the refresher first."
  if ! "$ROOT/scripts/refresh_kendra_desktop.sh" --no-restart >>"$LOG" 2>&1; then
    fail "The desktop app could not be rebuilt. See logs/desktop-launcher.log."
  fi
fi

LLAMA_SERVER="$ROOT/third_party/llama.cpp/build/bin/llama-server"
[ -x "$LLAMA_SERVER" ] || fail "llama-server is not built. Run scripts/bootstrap_intel_macos.sh."

TEXT_MODEL="$ROOT/models/qwen3-1.7b/Qwen3-1.7B-Q8_0.gguf" # pinned source; runtime Q4 auto-generates
[ -f "$TEXT_MODEL" ] || fail "Kendra's text brain is missing.
Run: .venv/bin/python scripts/fetch_local_models.py --llm"

if [ "$CHECK_ONLY" = "1" ]; then
  log "Preflight passed. Kendra is ready to launch with $CONFIG."
  exit 0
fi

# A half-dead service set makes `dev start` refuse to run. Normalize it here so a
# crashed previous session never blocks the next double-click.
STATUS="$("$ROOT/.venv/bin/python" -m kendra --config "$CONFIG" dev status 2>/dev/null || echo '{}')"
NEEDS_RESET="$(printf '%s' "$STATUS" | "$ROOT/.venv/bin/python" -c '
import json, sys
try:
    services = (json.load(sys.stdin).get("services") or {}).values()
except Exception:
    print("no"); raise SystemExit
alive = [s for s in services if s.get("alive")]
print("yes" if alive and len(alive) != len(list(services)) else "no")
' 2>/dev/null || echo no)"
if [ "$NEEDS_RESET" = "yes" ]; then
  log "Clearing a partially running Kendra stack before launch."
  "$ROOT/.venv/bin/python" -m kendra --config "$CONFIG" dev stop >>"$LOG" 2>&1 || true
fi

# ---- Launch ----------------------------------------------------------------
export KENDRA_CONFIG="$CONFIG"
export KENDRA_PYTHON="$ROOT/.venv/bin/python"
# Let the Python vision service open the webcam without trying to raise a TCC
# prompt from a worker thread; macOS attributes the prompt to the launching app.
export OPENCV_AVFOUNDATION_SKIP_AUTH="${OPENCV_AVFOUNDATION_SKIP_AUTH:-1}"

log "Starting the Kendra desktop app (Electron) with $CONFIG"
cd "$ROOT/dashboard" || fail "Missing dashboard directory"
exec ./node_modules/.bin/electron electron/main.mjs >>"$LOG" 2>&1
