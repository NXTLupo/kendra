# START HERE — Coding Agent Bootstrap for Kendra

You are taking over the **Kendra — The Sovereign AI Arachnid** codebase. Your job is to turn this unzipped repository into a verified local development environment on **Jonathan's Intel iMac (x86_64 macOS)**, push the code safely to **https://github.com/NXTLupo/kendra**, and then build and validate **Virtual Kendra** before any physical robot movement is enabled.

Read this entire file before changing code. Then read `README.md`, `docs/ARCHITECTURE.md`, `docs/KENDRA_BUILD_GUIDE_V3.md`, `docs/HARDWARE_GATES.md`, `docs/KENDRA_BRAIN.md`, `docs/INTEL_IMAC_VIRTUAL_KENDRA.md`, and `charter/charter.md`.

## Non-negotiable product requirements

Kendra is an offline-first intelligent hexapod companion. Treat these as invariants:

1. **All AI inference is local.** No hosted LLM, speech, vision, embedding, face recognition, or wake-word API. No API-key dependency, per-token fee, or per-minute speech fee. Localhost HTTP interfaces used by local processes are allowed; they are transport, not cloud services.
2. **Wake phrase is exactly `Kendra`.** It is recognized locally. Spoken stop is secondary; physical e-stop will be primary on the robot.
3. **Voice chat must prioritize low latency and interruptibility.** Local ASR, local LLM, local Piper TTS, sentence/phrase streaming where practical, and affect-driven prosody. Never add ElevenLabs, OpenAI Speech, Azure Speech, Google Speech, Amazon Polly, or another hosted voice service.
4. **Kendra Brain is native and application-independent.** It uses SQLite/FTS5 plus local embeddings. There is no Obsidian dependency, integration, plugin, vault, or required Markdown workflow. Backups are SQLite + JSONL.
5. **People recognition is local.** Camera -> face detection -> local embedding -> local identity resolver -> opaque `person_uid` -> Kendra Brain relationship/history context. Biometric vectors stay in `data/kendra-identities.db`, separate from `data/kendra-brain.db`. Enrollment requires explicit consent. Unknown faces remain unknown.
6. **Simulation comes before hardware.** The Intel iMac must be able to run the complete cognitive stack with a Webots digital twin. No real GPIO, PCA9685, servo, or battery dependency may be required for desktop testing.
7. **The digital twin represents the Adeept RaspClaws family, with the RaspClaws-Metal as the intended physical target.** The old Adeept gait code is useful reference material, but do not pretend its channel map proves the current Metal kit's 17-servo mapping. Keep the simulator's articulated model/config separate from the final verified physical mapping.
8. **Code is the cage.** The LLM proposes only whitelisted typed tools. The independent reflex layer can block movement. The body service treats a missing/stale reflex heartbeat as a stop condition. Never give the model shell access, arbitrary filesystem access, arbitrary network fetch, arbitrary recipients, or a safety bypass.
9. **Physical Kendra is fail-closed.** `project.mode: hardware` must continue to refuse movement until every hard hardware gate is true: servo mapping, battery path, e-stop, four cliff sensors, and motion calibration.
10. **Do not weaken safety to make a demo pass.** Fix the architecture or simulator instead.

## Phase 1 — Place the repository on the Intel iMac

Assume this ZIP may have been unpacked into `~/Downloads`. Put the project in the normal Git workspace:

```bash
mkdir -p ~/Documents/GitHub
cd ~/Documents/GitHub
```

If `~/Documents/GitHub/kendra` does not exist, move or copy the unzipped `kendra` directory there. If it already exists, do **not** delete it blindly. Inspect both copies and preserve any newer work.

Canonical local path after this step:

```text
~/Documents/GitHub/kendra
```

Then:

```bash
cd ~/Documents/GitHub/kendra
pwd
uname -s
uname -m
```

Required architecture for this workstation profile:

```text
Darwin
x86_64
```

Do not apply Apple Silicon `/opt/homebrew`, ARM64, or Rosetta-specific setup to this machine. Intel Homebrew normally lives under `/usr/local`.

## Phase 2 — Audit before editing

Before modifying anything:

```bash
find . -maxdepth 3 -type f | sort
grep -RniE 'obsidian|openai.*api|elevenlabs|azure.*speech|google.*speech|anthropic.*api' . \
  --exclude-dir=.git --exclude-dir=.venv --exclude-dir=.pytest_cache || true
```

The word `Obsidian` may appear only in an explicit statement that Kendra does **not** depend on it. There must be no Obsidian runtime integration.

Review the configuration hierarchy in `kendra/config.py`. Desktop testing uses `config/pc.yaml` or `config/webots.yaml`. Hardware configuration remains fail-closed.

## Phase 3 — Initialize Git and connect the target repository

Inspect Git state:

```bash
git status 2>/dev/null || true
git remote -v 2>/dev/null || true
```

If this ZIP already contains `.git`, preserve it. Otherwise:

```bash
git init -b main
```

Set the remote exactly:

```bash
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/NXTLupo/kendra.git
git remote -v
```

Check the remote before pushing:

```bash
git ls-remote origin
```

If the remote already contains commits, **fetch and reconcile without destroying remote history**:

```bash
git fetch origin --prune
```

Do not use `git push --force` or `git reset --hard origin/main` as a shortcut. Compare histories and merge/rebase intentionally. Preserve both the repository contents in this ZIP and legitimate remote work.

If the remote is empty, create the initial commit after all local verification below.

## Phase 4 — Bootstrap Intel macOS

Use the included bootstrap:

```bash
chmod +x scripts/*.sh
./scripts/bootstrap_intel_macos.sh
```

The script must verify `uname -m` is `x86_64`, use Intel-compatible Homebrew tooling, create `.venv`, install Kendra in editable mode, build local `llama.cpp` and `whisper.cpp`, provision public local models, initialize Kendra Brain, and run tests.

If a dependency no longer installs, fix the dependency in the repository rather than silently substituting a cloud service.

## Phase 5 — Install and launch Webots

Install the current Webots macOS application that supports this Intel Mac. Do not assume an ARM build.

Open:

```text
simulator/webots/worlds/kendra_virtual.wbt
```

The world should show Virtual Kendra, a platform edge, an obstacle, a human-sized target, and a visual home/perch target. The `kendra_bridge` controller exposes only localhost TCP at `127.0.0.1:8765`.

Validate the bridge before starting the body service. From the repository virtual environment, a simple socket health call should return JSON indicating `simulator: webots`.

The Webots driver must support the same high-level body contract as hardware:

```text
walk(direction, steps, speed)
turn(degrees, speed)
pose(name)
look(pan, tilt)
stop()
front_distance_cm()
battery_voltage()
```

Never use Webots geometry to invent the final real servo PWM centers, torque limits, or channel mapping. Those remain hardware-calibration facts.

## Phase 6 — Start the local language brain

Run in its own Terminal window:

```bash
cd ~/Documents/GitHub/kendra
./scripts/start_llm_intel_macos.sh
```

This must expose the local llama.cpp server only on `127.0.0.1:8080`. It uses the locally downloaded Qwen3-4B GGUF. It must not require an API key.

Check:

```bash
curl -fsS http://127.0.0.1:8080/health
```

## Phase 7 — Start local multimodal sight

Run in another Terminal window:

```bash
cd ~/Documents/GitHub/kendra
./scripts/start_vlm_intel_macos.sh
```

This is a **local** llama.cpp multimodal service on `127.0.0.1:8081`. The first provisioning run may download a public model; inference thereafter is local. No hosted inference request is allowed.

The vision service also independently supports local webcam capture, YuNet face detection, SFace identity embeddings, ArUco home-marker detection, and person counts. Semantic scene understanding uses the local multimodal endpoint when configured.

## Phase 8 — Initialize and start Virtual Kendra

With Webots and both local model servers running:

```bash
cd ~/Documents/GitHub/kendra
source .venv/bin/activate
python -m kendra --config config/webots.yaml init
python -m kendra --config config/webots.yaml doctor
python -m kendra --config config/webots.yaml dev start --voice
python -m kendra --config config/webots.yaml dev status
```

If `doctor` reports a missing optional component, resolve it. If it reports a required component missing, do not continue until fixed.

## Phase 9 — Validate the complete desktop stack

### 9.1 Text cognition

```bash
python -m kendra --config config/webots.yaml chat
```

Verify Kendra identifies herself consistently with the charter and can converse with networking disabled, except that live web research should truthfully report offline/unavailable.

### 9.2 Second Brain

```bash
python -m kendra --config config/webots.yaml brain stats
python -m kendra --config config/webots.yaml brain remember \
  "Jonathan prefers Kendra's AI inference to run locally." \
  --kind preference --provenance user_stated --confidence 1.0 --salience 0.9
python -m kendra --config config/webots.yaml brain search "local inference"
python -m kendra --config config/webots.yaml brain backup
python -m kendra --config config/webots.yaml brain export-jsonl
```

Confirm persistence after stopping/restarting the stack. Do not replace this system with a notes application.

### 9.3 People recognition

With Jonathan in front of the iMac webcam and after explicit consent:

```bash
python -m kendra --config config/webots.yaml vision enroll Jonathan \
  --relationship owner --frames 10 --consent
python -m kendra --config config/webots.yaml vision recognize
```

Verify the local identity DB contains multiple embeddings and Kendra Brain contains only the linked semantic relationship/history. Test an unknown person and confirm Kendra does not guess their identity.

### 9.4 Multimodal sight

```bash
python -m kendra --config config/webots.yaml vision observe --semantic \
  --question "Describe what is in front of you from your low robot viewpoint."
```

Confirm the image stays local and semantic inference goes to `127.0.0.1:8081`, not the public internet.

### 9.5 Voice

The wake phrase is:

```text
Kendra
```

Say it naturally, then speak a question. Verify:

- wake detection is local;
- user audio is transcribed locally;
- reasoning runs on local llama.cpp;
- response audio is synthesized locally with Piper;
- the selected affect changes local prosody appropriately;
- the physical/cloud network can be disconnected after models are provisioned and conversation still works;
- `Stop Kendra` remains a secondary software interrupt.

Measure end-of-speech -> first audible response latency and record it in `docs/TESTING.md` or a benchmark artifact. Optimize without replacing local components with paid services.

### 9.6 Digital twin movement

From chat, ask Kendra to move or use the body test tools. Verify Webots visibly performs walk, turn, pose, look, and stop actions. Drive the digital twin toward the platform edge and obstacle and prove the independent reflex layer blocks unsafe commands.

Kill the agent process while a simulated mission is active and verify reflex/body safety remains functional.

### 9.7 Offline invariant

Disconnect Wi-Fi/Ethernet after all local models are present. Repeat:

- `Kendra`
- ASR
- voice conversation
- Second Brain recall
- webcam sight
- face recognition
- Webots movement
- stop/reflex behavior

Only live web research and external photo delivery are allowed to degrade because they inherently need connectivity.

## Phase 10 — Run automated verification

At minimum:

```bash
PYTHONPATH=. pytest -q
python -m compileall -q kendra scripts
python scripts/verify_files.py
```

Also run Ruff if installed:

```bash
ruff check kendra tests scripts
```

Fix real errors. Do not suppress broad classes of failures just to get a green run.

## Phase 11 — Update documentation as you learn

If you fix anything during deployment, update the relevant documentation in the same commit. Keep these current:

- `README.md`
- `BUILD_STATE.md`
- `docs/ARCHITECTURE.md`
- `docs/INTEL_IMAC_VIRTUAL_KENDRA.md`
- `docs/HARDWARE_GATES.md`
- `docs/TESTING.md`
- `manifests/models.yaml`
- `manifests/software-lock.txt`

Record exact versions and SHA-256 values of models actually deployed. Do not put multi-GB model binaries in Git.

## Phase 12 — Commit and push to NXTLupo/kendra

Review first:

```bash
git status
git diff --check
git diff --stat
```

Ensure secrets, model weights, databases, photos, logs, and runtime sockets are ignored.

Then commit:

```bash
git add .
git commit -m "Build Kendra local AI runtime and Virtual Kendra digital twin"
```

If `origin/main` exists, synchronize safely before push. Then:

```bash
git push -u origin main
```

If GitHub authentication is required, use Jonathan's existing GitHub credential manager, SSH configuration, or GitHub CLI login. Never put a token in this repository, a config YAML, a prompt, or shell history.

## Phase 13 — Do not enable the physical body yet

Virtual Kendra is the development target until the physical RaspClaws-Metal is assembled and the hard gates are verified from the exact hardware:

```bash
python -m kendra --config config/production.example.yaml gates
```

Real motion must remain disabled while any gate is false. In particular, do not infer the current RaspClaws-Metal 17-servo mapping from the legacy Adeept RaspClaws code. Verify the actual current kit wiring and charging topology first, document them, and only then create `config/hardware.local.yaml` and enable `body.driver: raspclaws`.

## Definition of done for this workstation

You are done with the **Virtual Kendra** milestone only when all of the following are demonstrated on the Intel iMac:

- repository is clean, tested, committed, and pushed to `NXTLupo/kendra`;
- no hosted AI/speech/vision API is required;
- `Kendra` wakes her locally;
- local voice conversation works end to end;
- affect-driven local voice inflection works;
- Kendra Brain persists through reboot/service restart;
- local person enrollment and recognition works with consent;
- unknown people remain unknown;
- semantic webcam vision works through a local VLM;
- Webots displays Virtual Kendra and receives the same high-level body commands as the future robot;
- obstacle/cliff reflexes stop simulated motion independently of the LLM;
- offline mode preserves cognition, speech, memory, vision, identity, and simulation;
- all automated tests pass;
- physical movement remains fail-closed.

Do not report success based only on imports or unit tests. Demonstrate the end-to-end behaviors above and record any measured limitations honestly.
