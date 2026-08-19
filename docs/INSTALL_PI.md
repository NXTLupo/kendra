# Raspberry Pi 5 Software Installation

This document begins after the mechanical/safety instructions in `KENDRA_BUILD_GUIDE_V3.md`.

## 1. Work in simulation first

On the Pi:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git python3 python3-venv python3-pip build-essential cmake ninja-build \
  pkg-config libopenblas-dev sqlite3 alsa-utils ffmpeg curl jq
```

Clone the repository:

```bash
cd ~
git clone https://github.com/NXTLupo/kendra.git
cd kendra
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[brain,hardware,dev]'
```

For camera support on Raspberry Pi OS, prefer the distro Picamera2 package:

```bash
sudo apt install -y python3-picamera2 python3-opencv
```

If you use the pip OpenCV vision extra instead, understand that Raspberry Pi camera integration differs from a normal USB webcam.

Initialize safe local state:

```bash
kendra init
kendra gates
pytest -q
```

## 2. Build llama.cpp

The project intentionally does not float against `master`. Set a qualified commit in `manifests/software-lock.txt`, then run the provided build helper.

```bash
./scripts/build_llama_cpp.sh
```

Kendra's always-on brain is **Qwen3-1.7B Q8_0** (about 1.8 GB). It is the same
artifact and the same llama.cpp architecture as the Intel iMac, so the Pi runs
its own inference and is never tethered to the Mac:

```bash
.venv/bin/python scripts/fetch_local_models.py --llm
sha256sum models/qwen3-1.7b/Qwen3-1.7B-Q8_0.gguf
```

Then generate the faster runtime quantization locally (never downloaded):

```bash
cmake --build third_party/llama.cpp/build --config Release --target llama-quantize -j "$(nproc)"
third_party/llama.cpp/build/bin/llama-quantize --allow-requantize \
  models/qwen3-1.7b/Qwen3-1.7B-Q8_0.gguf \
  models/qwen3-1.7b/Qwen3-1.7B-Q4_K_M.requant.gguf Q4_K_M
```

The Q8_0 source hash must match `qwen3-1.7b_q8_0_source_gguf_sha256` in
`manifests/software-lock.txt`. Do **not** install Qwen3-4B on the Pi; it is an
optional desktop-only deep-reasoning model that breaks voice latency parity.

## 3. Build whisper.cpp and install ASR/Piper assets

Kendra's ASR is **whisper.cpp**, chosen precisely because it builds from source
on aarch64 Linux as well as x86_64 macOS. Do not switch this profile to
Moonshine: it publishes no Linux aarch64 wheel and cannot load on the Pi.

```bash
git clone https://github.com/ggml-org/whisper.cpp.git third_party/whisper.cpp
git -C third_party/whisper.cpp checkout --detach v1.9.1
cmake -S third_party/whisper.cpp -B third_party/whisper.cpp/build \
  -DWHISPER_COREML=OFF -DGGML_METAL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build third_party/whisper.cpp/build --config Release \
  --target whisper-cli whisper-server -j "$(nproc)"
```

`whisper-server` keeps the ASR model resident (about 0.8 s saved per spoken
turn); install `systemd/kendra-asr.service` with the other units. `whisper-cli`
remains the automatic fallback whenever the server is down.

Then fetch the voice assets:

```bash
.venv/bin/python scripts/fetch_local_models.py --voice
```

Before starting voice, confirm all four:

```bash
ls -l third_party/whisper.cpp/build/bin/whisper-cli
ls -l models/whisper/ggml-base.en.bin
ls -l models/piper/en_US-amy-medium/en_US-amy-medium.onnx
ls -d models/vosk/vosk-model-small-en-us-0.15
```

### Optional voice organs (ST Micro benchmark round, all CPU/ONNX — Pi-clean)

- **Kokoro TTS** (humanlike voice, MOS 4.02 vs Piper 2.85; measured RTF ~1.0
  on the Intel iMac under load, expect worse on Pi — qualify on the bench
  before adopting): `pip install kokoro-onnx`, then
  `scripts/fetch_local_models.py --kokoro`, then set
  `voice.tts.provider: kokoro_onnx`. CPU only; never route to a GPU delegate.
- **Moonshine Base ASR** (RAM relief: ~250 MB resident vs Parakeet's ~700 MB,
  WER 0.051 vs 0.026): `scripts/fetch_local_models.py --moonshine`, then set
  `voice.asr.provider: moonshine_onnx`. Self-contained onnxruntime loader —
  no extra pip packages. Use only if the 16 GB budget tightens.
- **LFM2-8B-A1B brain candidate** (1B-active MoE): fetch with
  `scripts/fetch_local_models.py --lfm2` and A/B via
  `KENDRA_LLM_MODEL=models/lfm2-8b-a1b/LFM2-8B-A1B-Q4_K_M.gguf` on the LLM
  unit. Adds ~2.2 GB over Gemma (mind ≈ 9.7 GB resident — still inside
  16 GB with zram). Do not adopt for the planner until its tool syntax is
  adapted: LFMs emit Pythonic calls, not JSON (37.5% vs 90% format success).

`kendra doctor` verifies that the selected ASR engine can actually load, not
merely that its files exist.

Run:

```bash
kendra doctor
```

Missing model assets should appear as failed checks rather than being silently downloaded at runtime.

## 4. Kendra Brain

The hashing retrieval provider requires no model download:

```bash
kendra brain stats
kendra brain remember "Kendra's brain is local." --provenance system
kendra brain search "brain local"
kendra brain backup
```

After you place a local MiniLM directory at the configured path, install the optional dependency and switch the provider:

```bash
pip install -e '.[brain]'
```

## 5. SearXNG and Kiwix

Start the local SearXNG stack:

```bash
cd searxng
docker compose up -d
```

Run Kiwix server separately against your verified ZIM directory, bound locally. Update `manifests/kiwix.yaml` with filename, archive date, size, and SHA-256.

## 6. Development service startup

For initial qualification use separate terminals:

```bash
kendra service reflex
kendra service body
kendra service brain
kendra service research
kendra service vision
kendra service leds
kendra service delivery
kendra service agent
kendra service voice
```

Start autonomy only after manual qualification and only when enabled in local config.

## 7. Production systemd

After every acceptance test passes, use `scripts/install_systemd.sh` and adapt `/etc/kendra/production.yaml` from `config/production.example.yaml`.

Keep durable state under `/var/lib/kendra`, not inside an application slot.


## 8. Memory budget and swap policy

The Pi 5 has 8 GB. Kendra's resident mind, measured on the iMac with the same
artifacts:

| Component | Approx. resident |
|---|---:|
| Raspberry Pi OS Lite (headless) | ~0.4 GB |
| Qwen3-1.7B Q4_K_M + KV (llama.cpp) | ~1.8 GB |
| whisper-server (base.en) | ~0.5 GB |
| MiniLM ONNX embeddings | ~0.1 GB |
| Ten Kendra services (Python) | ~1.5 GB |
| SearXNG container (native, no VM) | ~0.3 GB |
| **Total without vision** | **~4.6 GB** |
| Moondream2 vision (resident) | +~2.0 GB |

With Moondream resident the total is ~6.6 GB on an 8 GB Pi — workable
headroom. Install `systemd/kendra-vlm.service` with the other units; its OOM
score means vision is sacrificed before speech, and speech before safety.
Never run the retired Qwen-VL on the Pi (~3.5 GB resident leaves nothing).

Swap policy: never swap to SD card (destroys cards, freezes inference). Use
zram only:

```bash
sudo apt install -y zram-tools
echo -e "ALGO=zstd\nPERCENT=25" | sudo tee /etc/default/zramswap
sudo systemctl restart zramswap
```

The systemd units carry an OOM policy: under memory pressure Linux kills the
LLM or ASR first (speech fails, recoverable via Restart=always) and protects
`kendra-reflex`/`kendra-body` (OOMScoreAdjust -900/-800) — senses degrade,
safety never does. A stale reflex heartbeat still stops all motion regardless.
