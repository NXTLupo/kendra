#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is for macOS. Use scripts/bootstrap_dev.sh on other systems." >&2
  exit 2
fi

ARCH="$(uname -m)"
if [ "$ARCH" != "x86_64" ]; then
  echo "Expected an Intel Mac (x86_64), but uname -m returned: $ARCH" >&2
  echo "Use the platform-appropriate bootstrap instead of forcing Rosetta." >&2
  exit 2
fi

echo "Kendra Intel-macOS bootstrap"
echo "Repository: $ROOT"
echo "Architecture: $ARCH"

if ! command -v brew >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Homebrew is required for the automated Intel Mac setup.
Install Homebrew from its official installer, then rerun this script.
EOF
  exit 3
fi

BREW_PREFIX="$(brew --prefix)"
echo "Homebrew prefix: $BREW_PREFIX"
if [ "$BREW_PREFIX" != "/usr/local" ]; then
  echo "WARNING: Intel Homebrew normally uses /usr/local. Continuing with $BREW_PREFIX." >&2
fi

brew update
brew install python@3.12 cmake git git-lfs ffmpeg minisign node@22 portaudio pkg-config wget

PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
"$PYTHON" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[dev,vision,voice]'

export PATH="$(brew --prefix node@22)/bin:$PATH"
(cd dashboard && npm ci --ignore-scripts --no-audit --no-fund)

mkdir -p third_party models runtime logs data exports photos outbox

LLAMA_COMMIT="$(sed -n 's/^llama.cpp_commit=//p' manifests/software-lock.txt | head -n1)"
if [ -z "$LLAMA_COMMIT" ] || [ "$LLAMA_COMMIT" = "UNPINNED_UNTIL_QUALIFIED" ]; then
  echo "Refusing to build floating llama.cpp. Pin an exact commit in manifests/software-lock.txt." >&2
  exit 4
fi
if [ ! -d third_party/llama.cpp/.git ]; then
  git clone https://github.com/ggml-org/llama.cpp.git third_party/llama.cpp
else
  git -C third_party/llama.cpp fetch origin
fi
git -C third_party/llama.cpp checkout --detach "$LLAMA_COMMIT"
cmake -S third_party/llama.cpp -B third_party/llama.cpp/build -DGGML_METAL=OFF -DGGML_BLAS=ON -DCMAKE_BUILD_TYPE=Release
cmake --build third_party/llama.cpp/build --config Release --target llama-server llama-bench -j "$(sysctl -n hw.logicalcpu)"

if [ ! -d third_party/whisper.cpp/.git ]; then
  git clone https://github.com/ggml-org/whisper.cpp.git third_party/whisper.cpp
else
  git -C third_party/whisper.cpp fetch --tags origin
fi
git -C third_party/whisper.cpp checkout --detach v1.9.1
cmake -S third_party/whisper.cpp -B third_party/whisper.cpp/build -DWHISPER_COREML=OFF -DGGML_METAL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build third_party/whisper.cpp/build --config Release --target whisper-cli -j "$(sysctl -n hw.logicalcpu)"

python scripts/fetch_local_models.py --core --vlm
python -m kendra --config config/pc.yaml init
PYTHONPATH=. pytest -q
ruff check kendra tests scripts
python -m compileall -q kendra scripts simulator/webots/controllers
python scripts/verify_files.py
(cd dashboard && npm run build && npm test)

cat <<'EOF'

Intel Mac bootstrap complete.

Next:
  1. Install Webots using the official macOS Intel build/application.
  2. Open simulator/webots/worlds/kendra_virtual.wbt in Webots.
  3. Start the local llama.cpp server with scripts/start_llm_intel_macos.sh.
  4. Start Kendra services: .venv/bin/python -m kendra --config config/webots.yaml dev start --voice
  5. Open the local dashboard: scripts/start_kendra_dashboard_intel_macos.sh
  6. Test text chat:      .venv/bin/python -m kendra --config config/webots.yaml chat
  7. Test voice:          say "Kendra" after the voice service is ready.

No cloud AI API is required by this workflow.
EOF
