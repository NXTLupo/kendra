#!/usr/bin/env bash
# Kendra desktop refresher.
#
# Rebuilds the Electron desktop app after source changes and restarts Virtual
# Kendra cleanly. Use this instead of remembering the npm/pytest/dev-stack
# incantations by hand.
#
#   scripts/refresh_kendra_desktop.sh              # rebuild + verify + restart
#   scripts/refresh_kendra_desktop.sh --no-restart # rebuild + verify only
#   scripts/refresh_kendra_desktop.sh --deps       # force npm ci + pip reinstall
#   scripts/refresh_kendra_desktop.sh --fast       # rebuild + restart, skip checks
#
# Environment:
#   KENDRA_CONFIG   Kendra profile (default config/pc.yaml)
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

RESTART=1
FORCE_DEPS=0
RUN_CHECKS=1
for argument in "$@"; do
  case "$argument" in
    --no-restart) RESTART=0 ;;
    --deps) FORCE_DEPS=1 ;;
    --fast) RUN_CHECKS=0 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $argument" >&2; exit 2 ;;
  esac
done

CONFIG="${KENDRA_CONFIG:-config/pc.yaml}"
PYTHON="$ROOT/.venv/bin/python"
STAMP="$ROOT/dashboard/node_modules/.kendra-deps-stamp"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mRefresh failed: %s\033[0m\n' "$*" >&2; exit 2; }

[ "$(uname -s)" = "Darwin" ] || die "This refresher targets macOS."
[ -x "$PYTHON" ] || die "Missing .venv. Run scripts/bootstrap_intel_macos.sh first."

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
if [ -x /usr/local/bin/brew ] && NODE22="$(/usr/local/bin/brew --prefix node@22 2>/dev/null)"; then
  [ -d "$NODE22/bin" ] && export PATH="$NODE22/bin:$PATH"
fi
command -v npm >/dev/null 2>&1 || die "Node.js/npm not found. Run: brew install node@22"

step "Stopping the running Kendra desktop app and services"
pkill -f "dashboard/electron/main.mjs" 2>/dev/null || true
pkill -f "electron electron/main.mjs" 2>/dev/null || true
"$PYTHON" -m kendra --config "$CONFIG" dev stop >/dev/null 2>&1 || true
echo "stopped"

# Reinstall node dependencies only when the lockfile actually changed.
NEED_DEPS=$FORCE_DEPS
if [ ! -d dashboard/node_modules ]; then
  NEED_DEPS=1
elif [ ! -f "$STAMP" ] || [ dashboard/package-lock.json -nt "$STAMP" ]; then
  NEED_DEPS=1
fi
if [ "$NEED_DEPS" = "1" ]; then
  step "Installing desktop dependencies (package-lock.json changed)"
  (cd dashboard && npm ci --ignore-scripts --no-audit --no-fund) || die "npm ci failed"
  # Electron ships a postinstall that fetches its binary; --ignore-scripts skips it.
  if [ ! -d dashboard/node_modules/electron/dist ]; then
    step "Fetching the Electron runtime binary"
    (cd dashboard && node node_modules/electron/install.js) || die "Could not install the Electron runtime"
  fi
  touch "$STAMP"
else
  step "Desktop dependencies are current"
fi

if [ "$FORCE_DEPS" = "1" ]; then
  step "Reinstalling the Kendra Python package"
  "$PYTHON" -m pip install -q -e '.[dev,vision,voice]' || die "pip install failed"
fi

step "Building the Kendra renderer"
(cd dashboard && npm run build) || die "vite build failed"

if [ "$RUN_CHECKS" = "1" ]; then
  step "Verifying the desktop bundle"
  (cd dashboard && npm test) || die "desktop bundle tests failed"
  (cd dashboard && npm run lint) || die "eslint failed"

  step "Verifying the Kendra runtime"
  "$PYTHON" -m pytest -q || die "pytest failed"
  "$ROOT/.venv/bin/ruff" check kendra tests scripts || die "ruff failed"
fi

step "Checking local model and asset readiness ($CONFIG)"
"$PYTHON" -m kendra --config "$CONFIG" doctor 2>/dev/null | "$PYTHON" -c '
import json, sys
try:
    report = json.load(sys.stdin)
except Exception:
    print("  doctor did not return a report"); raise SystemExit
checks = report.get("checks", {})
for key in report.get("required_checks", []):
    entry = checks.get(key, {})
    print(("  ok   " if entry.get("ok") else "  FAIL ") + key)
print(f"\n  install_ready={report.get(\"install_ready\")}  live_stack_ready={report.get(\"live_stack_ready\")}")
' || true

if [ "$RESTART" = "0" ]; then
  step "Rebuild complete (not restarting)"
  exit 0
fi

step "Relaunching the Kendra desktop app"
exec "$ROOT/scripts/kendra_desktop_launcher.sh" "$CONFIG"
