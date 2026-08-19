# Virtual Kendra on an Intel iMac

This is the desktop integration target for Kendra before physical-body deployment. The target machine is an **Intel iMac running macOS (`x86_64`)**, not Apple Silicon.

## What runs locally

Virtual Kendra is designed to run the same cognitive/control architecture that will later move to the Raspberry Pi:

- Qwen text reasoning through a localhost `llama.cpp` process;
- local Moonshine streaming ASR;
- exact local wake phrase `Kendra` using a constrained Vosk grammar;
- local Piper speech synthesis with affect/prosody profiles;
- native Kendra Brain SQLite/FTS5 persistent memory;
- separate local biometric identity SQLite store;
- webcam vision, YuNet/SFace recognition, ArUco detection;
- optional semantic vision through a second localhost multimodal `llama.cpp` process;
- research adapters, with offline Kiwix fallback when installed;
- agent/tool validation;
- safety/reflex process;
- Webots RaspClaws-family digital twin instead of physical motors.

No hosted AI API is part of this development profile.

## Intel-specific assumptions

Check the machine first:

```bash
uname -s
uname -m
```

Expected:

```text
Darwin
x86_64
```

Intel Homebrew commonly uses `/usr/local`. Do not copy Apple Silicon `/opt/homebrew` or Rosetta instructions into this profile.

## Bootstrap

From the repository root:

```bash
./scripts/bootstrap_intel_macos.sh
```

This creates `.venv`, installs Python dependencies, builds local `llama.cpp` and `whisper.cpp`, downloads public runtime model assets, initializes local data stores, and runs unit tests.

Large model binaries are deliberately excluded from Git. They live under `models/` and are ignored.

## Webots

Open:

```text
simulator/webots/worlds/kendra_virtual.wbt
```

The controller `simulator/webots/controllers/kendra_bridge/kendra_bridge.py` listens only on `127.0.0.1:8765`. `kendra/body/webots.py` converts the normal Kendra Body API into bridge commands.

The current digital twin is a behavioral/kinematic model of the RaspClaws family. It is intentionally **not** the authority for the final Metal kit's servo channel map. When the real chassis is assembled, measure its dimensions, verify the current servo map, and calibrate the digital twin against the physical unit.

## Local inference processes

Terminal A:

```bash
./scripts/start_llm_intel_macos.sh
```

Terminal B:

```bash
./scripts/start_vlm_intel_macos.sh
```

Terminal C, after opening Webots:

```bash
source .venv/bin/activate
python -m kendra --config config/webots.yaml init
python -m kendra --config config/webots.yaml doctor
python -m kendra --config config/webots.yaml dev start --voice
```

Stop the stack with:

```bash
./scripts/stop_virtual_kendra.sh
```

## Local-only network boundary

These loopback ports are expected during desktop development:

| Port | Process | Purpose |
|---:|---|---|
| 8080 | llama.cpp | text reasoning |
| 8081 | llama.cpp | multimodal scene reasoning |
| 8765 | Webots bridge | digital-twin body control |
| 8888 | SearXNG, optional | live search aggregation |
| 8090 | Kiwix, optional | offline knowledge lookup |

The first three are bound to localhost. They are local IPC even though HTTP/TCP is used as the transport.

## Acceptance tests

Before moving to physical hardware, prove on this iMac that Kendra can:

1. wake on `Kendra`;
2. conduct an end-to-end local spoken conversation;
3. use affect-driven Piper prosody;
4. remember and recall information across service restarts;
5. enroll and recognize a consenting person locally;
6. leave an unknown face unidentified;
7. describe a webcam scene through the local VLM;
8. walk/turn/look/pose in Webots;
9. stop the digital twin at a simulated cliff/obstacle even when the agent is killed;
10. continue the core interaction loop with the external network disconnected.

Record latency, CPU/RAM pressure, thermal behavior, and model versions. Those measurements determine what must be reduced or optimized for the Raspberry Pi later.
