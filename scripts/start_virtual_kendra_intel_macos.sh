#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "This launcher is specifically for an Intel iMac (x86_64 macOS)." >&2
  exit 2
fi
if [ ! -x .venv/bin/python ]; then
  echo "Missing .venv. Run scripts/bootstrap_intel_macos.sh first." >&2
  exit 2
fi
exec .venv/bin/python -m kendra --config config/webots.yaml dev start --voice
