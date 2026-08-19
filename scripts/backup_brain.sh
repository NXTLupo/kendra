#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -x .venv/bin/kendra ]; then
  exec .venv/bin/kendra brain backup
fi
exec kendra brain backup
