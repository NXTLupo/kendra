#!/usr/bin/env bash
# Start Kendra's semantic vision server: Moondream2 on 127.0.0.1:17801.
# Chosen for Pi parity: ~2 GB resident and ~4 s warm sight on the iMac,
# light enough to run on the 8 GB robot alongside her whole mind. The Pi
# runs the identical artifacts under systemd/kendra-vlm.service.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SERVER="third_party/llama.cpp/build/bin/llama-server"
SOURCE_MODEL="models/moondream2/moondream2-text-model-f16.gguf"   # pinned
MODEL="models/moondream2/moondream2-text-model-Q4_K_M.gguf"       # generated locally
MMPROJ="models/moondream2/moondream2-mmproj-f16.gguf"             # pinned
if [ ! -x "$SERVER" ]; then
  echo "Missing llama-server. Run scripts/bootstrap_intel_macos.sh first." >&2
  exit 2
fi
if [ ! -f "$SOURCE_MODEL" ] || [ ! -f "$MMPROJ" ]; then
  echo "Missing Moondream artifacts. Run: .venv/bin/python scripts/fetch_local_models.py --vlm" >&2
  exit 2
fi
if [ ! -f "$MODEL" ]; then
  echo "Generating runtime Q4_K_M from the pinned f16 (one time)..." >&2
  third_party/llama.cpp/build/bin/llama-quantize "$SOURCE_MODEL" "$MODEL" Q4_K_M >&2
fi
# --no-jinja: Moondream's vicuna-style template drops the image marker under
# the jinja engine; the legacy path places media correctly.
exec "$SERVER" -m "$MODEL" --mmproj "$MMPROJ" --host 127.0.0.1 --port 17801 -c 2048 -np 1 \
  --threads 4 --no-jinja --mlock \
  --cors-origins localhost --no-cors-credentials
