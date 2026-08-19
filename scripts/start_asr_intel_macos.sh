#!/usr/bin/env bash
# Start Kendra's persistent local whisper.cpp ASR server on 127.0.0.1:17802.
# Keeping the model resident saves ~0.8s of dead air on every spoken turn.
# The Raspberry Pi runs the same binary under systemd/kendra-asr.service.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SERVER="third_party/whisper.cpp/build/bin/whisper-server"
MODEL="models/whisper/ggml-small.en.bin"
if [ ! -x "$SERVER" ]; then
  echo "Missing whisper-server. Build it with:" >&2
  echo "  cmake --build third_party/whisper.cpp/build --config Release --target whisper-server" >&2
  exit 2
fi
if [ ! -f "$MODEL" ]; then
  echo "Missing model: $MODEL. Run: .venv/bin/python scripts/fetch_local_models.py --voice" >&2
  exit 2
fi
CPU_THREADS="$(sysctl -n hw.logicalcpu 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
exec "$SERVER" -m "$MODEL" --host 127.0.0.1 --port 17802 -t "$CPU_THREADS"
