#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/manifests/software-lock.txt"
COMMIT="$(sed -n 's/^llama.cpp_commit=//p' "$LOCK" | head -n1)"
if [ -z "$COMMIT" ] || [ "$COMMIT" = "UNPINNED_UNTIL_QUALIFIED" ]; then
  echo "Refusing to build floating llama.cpp. Put an exact qualified commit in manifests/software-lock.txt." >&2
  exit 2
fi
mkdir -p "$ROOT/third_party"
if [ ! -d "$ROOT/third_party/llama.cpp/.git" ]; then
  git clone https://github.com/ggml-org/llama.cpp.git "$ROOT/third_party/llama.cpp"
fi
git -C "$ROOT/third_party/llama.cpp" fetch --tags origin
git -C "$ROOT/third_party/llama.cpp" checkout --detach "$COMMIT"
cmake -S "$ROOT/third_party/llama.cpp" -B "$ROOT/third_party/llama.cpp/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build "$ROOT/third_party/llama.cpp/build" --target llama-server llama-bench -j "$(nproc)"
