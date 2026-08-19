#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CONFIG="${1:-config/pc.yaml}"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "This launcher is specifically for an Intel iMac (x86_64 macOS)." >&2
  exit 2
fi
if [ ! -x .venv/bin/python ]; then
  echo "Missing .venv. Run scripts/bootstrap_intel_macos.sh first." >&2
  exit 2
fi
if [ ! -f "$CONFIG" ]; then
  echo "Missing Kendra configuration: $CONFIG" >&2
  exit 2
fi
if command -v brew >/dev/null 2>&1 && brew --prefix node@22 >/dev/null 2>&1; then
  export PATH="$(brew --prefix node@22)/bin:$PATH"
fi
if [ ! -d dashboard/node_modules ] || [ ! -f dashboard/dist/index.html ]; then
  echo "Desktop dependencies/build are missing. Run scripts/bootstrap_intel_macos.sh first." >&2
  exit 2
fi

export KENDRA_CONFIG="$CONFIG"
export KENDRA_PYTHON="$ROOT/.venv/bin/python"
echo "Opening Kendra as a native macOS app using $CONFIG"
cd dashboard
exec npm run start
