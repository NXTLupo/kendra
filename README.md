# Kendra — The Sovereign AI Arachnid

Kendra is an offline-first intelligent hexapod companion. This repository contains the complete application architecture for **Virtual Kendra** on a desktop computer and the fail-closed path toward the physical Adeept RaspClaws-Metal robot.

The intended desktop development machine for the current handoff is an **Intel iMac (`x86_64` macOS)**. The intended robot computer is a Raspberry Pi 5.

## Start here

If you are a coding agent taking over this project, read:

**[`START_HERE_CODING_AGENT.md`](START_HERE_CODING_AGENT.md)**

If you are building manually, read:

1. `docs/INTEL_IMAC_VIRTUAL_KENDRA.md`
2. `docs/ARCHITECTURE.md`
3. `docs/KENDRA_BRAIN.md`
4. `docs/HARDWARE_GATES.md`
5. `docs/KENDRA_BUILD_GUIDE_V3.md`

## Core invariants

- Local AI inference only. No hosted LLM/speech/vision/embedding API is required.
- Wake phrase is **`Kendra`**.
- Voice path: local wake detection -> local audio capture/VAD -> Moonshine ASR -> local llama.cpp -> streamed local Piper synthesis.
- Affect labels control local Piper prosody; they are not claims that a model literally experiences emotions.
- Kendra Brain is native SQLite/FTS5 + local embeddings. It does not require a note-taking application.
- Person recognition is local and consent-gated. Biometric embeddings live in a separate identity database.
- The LLM can only propose typed whitelisted tools.
- Reflex safety runs independently of the LLM/agent.
- A stale or missing reflex heartbeat disables motion.
- Physical mode remains disabled until every hardware gate is verified on the exact robot.

## Repository map

```text
kendra/
├── START_HERE_CODING_AGENT.md
├── README.md
├── BUILD_STATE.md
├── pyproject.toml
├── charter/
├── config/
│   ├── default.yaml
│   ├── pc.yaml
│   ├── webots.yaml
│   └── production.example.yaml
├── docs/
├── dashboard/              # private local iMac UI
├── hardware/
├── kendra/
│   ├── agent/
│   ├── autonomy/
│   ├── body/
│   ├── brain/
│   ├── delivery/
│   ├── health/
│   ├── identity/
│   ├── leds/
│   ├── reflex/
│   ├── research/
│   ├── updates/
│   ├── vision/
│   └── voice/
├── manifests/
├── scripts/
├── searxng/
├── simulator/webots/
├── systemd/
└── tests/
```

Large models, databases, runtime sockets, logs, photos, local secrets, and audited vendor hardware code are intentionally excluded by `.gitignore`.

## Intel iMac quick start

From the repository root:

```bash
./scripts/bootstrap_intel_macos.sh
```

Then open this world in Webots:

```text
simulator/webots/worlds/kendra_virtual.wbt
```

Start the local text model:

```bash
./scripts/start_llm_intel_macos.sh
```

Start the optional local multimodal model in a second Terminal:

```bash
./scripts/start_vlm_intel_macos.sh
```

Start Virtual Kendra:

```bash
source .venv/bin/activate
python -m kendra --config config/webots.yaml init
python -m kendra --config config/webots.yaml doctor
python -m kendra --config config/webots.yaml dev start --voice
```

Open the private iMac dashboard in another Terminal:

```bash
./scripts/start_kendra_dashboard_intel_macos.sh
```

The desktop app is a native Electron window, not a web page: there is no dashboard HTTP server and no `127.0.0.1:3000`/`8766` listener. Its sandboxed renderer reaches Python only through named Electron IPC commands carried over a private stdio bridge. Use `config/webots.yaml` whenever the **3D Virtual Kendra body** should receive walk, turn, look, pose, and stop commands.

Status:

```bash
python -m kendra --config config/webots.yaml dev status
```

Text chat:

```bash
python -m kendra --config config/webots.yaml chat
```

Stop:

```bash
./scripts/stop_virtual_kendra.sh
```

## Virtual Kendra

The Webots digital twin receives the same high-level body verbs as the future physical driver:

```text
walk
turn
pose
look
stop
```

The world contains a platform edge, obstacle, human-sized target, and home/perch target. Four simulated cliff positions and front-range telemetry feed the same independent reflex layer used by the physical architecture.

The six virtual legs use visible coxa/femur/tibia articulation so gait behavior can be evaluated before hardware. The simulation is **not** the authority for the current RaspClaws-Metal's final 17-servo channel map, servo centers, torque limits, or battery topology. Those are physical verification gates.

## Local voice

Default wake phrase:

```text
Kendra
```

The wake detector uses a local Vosk grammar by default. User speech is transcribed locally with Moonshine. A short schema-constrained local planner decision is made first; the final spoken answer then streams from llama.cpp into phrase-sized Piper synthesis chunks. This lets speech begin before the entire response has generated.

Local affect profiles currently include:

- neutral
- warm
- curious
- concern
- alert
- delighted
- reflective

The secondary spoken stop phrases are `Stop Kendra`, `Kendra stop`, and `stop`. The physical emergency stop remains the real kill switch on the robot.

The dashboard's **Talk with Kendra** button provides push-to-talk voice chat on the iMac. The voice service pauses wake listening during the manual capture so the microphone is not opened twice. **Use my webcam** captures from the iMac camera, runs face/perch processing locally, and can ask the loopback VLM for a scene description.

## Kendra Brain

Canonical development database:

```text
data/kendra-brain.db
```

It stores episodes, facts, preferences, relationships, places, interests, goals, open questions, self-model state, reflections, turns, links, and cognitive events with provenance/confidence.

Useful commands:

```bash
python -m kendra --config config/pc.yaml brain stats
python -m kendra --config config/pc.yaml brain search "query"
python -m kendra --config config/pc.yaml brain backup
python -m kendra --config config/pc.yaml brain export-jsonl
```

## Local people recognition

Biometric data is deliberately separate:

```text
data/kendra-identities.db
```

Enrollment requires an explicit `--consent` flag after the person actually agrees:

```bash
python -m kendra --config config/webots.yaml vision enroll Jonathan \
  --relationship owner --frames 10 --consent
```

Recognition:

```bash
python -m kendra --config config/webots.yaml vision recognize
```

Unknown matches below threshold remain unknown. Identity embeddings are never sent to a hosted recognition service.

## Second Brain transfer

The dashboard can search and back up the active local brain, then merge memories from Kendra's physical body without copying biometric identity data:

- **Cable/removable drive:** export Kendra Brain JSONL on the robot and choose that file in the dashboard.
- **Local Wi-Fi:** pre-authorize an SSH key and host key, then use **Retrieve memories now**. The transfer runs over encrypted, strict-host-checked SSH and calls the robot's fixed `brain export-jsonl --stdout` command.

Imported memories are re-embedded locally, retain provenance and original timestamps in metadata, and are deduplicated. See `docs/DASHBOARD.md` for setup.

## Intelligence upgrade channel

Development stays on the iMac: improve and test Kendra here, commit, and push to the pinned GitHub repository. Kendra exposes typed voice tools for checking and requesting an intelligence upgrade, but installation is fail-closed:

- only the fixed `NXTLupo/kendra` GitHub release paths are accepted;
- a minisign signature and archive SHA-256 must verify;
- the release is built in the inactive A/B slot;
- unsigned Git code is never activated;
- voice installation remains disabled until your private release key is created outside Git and its public key is configured.

After that one-time key setup, `scripts/build_signed_intelligence_release.sh` prepares a release for review and push. The exact spoken confirmation is `install signed intelligence upgrade`.

## Tests

```bash
PYTHONPATH=. pytest -q
python -m compileall -q kendra scripts
python scripts/verify_files.py
```

The repository tests never energize physical hardware.

## Git target

Canonical remote:

```text
https://github.com/NXTLupo/kendra.git
```

The coding-agent handoff contains safe initialization/fetch/push instructions. Do not force-push over existing remote history.

## Physical deployment

Physical deployment is intentionally a later milestone. Before `body.driver: raspclaws` may be enabled, the exact robot must have verified evidence for:

- current servo/channel mapping;
- battery charging/protection topology;
- physical emergency stop and fuse;
- four cliff sensors;
- motion calibration;
- combined power/load tests.

Check gates with:

```bash
python -m kendra --config config/production.example.yaml gates
```

If any gate is false, real motion remains disabled by design.
