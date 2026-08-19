# Kendra Qualification and Regression Tests

## Automated tests

Run from the repository virtual environment:

```bash
pytest -q
ruff check .
```

Automated tests cover deterministic code. They do not prove the physical robot is safe.

## Simulation acceptance

Start reflex and body in simulation, then verify:

```bash
kendra service reflex
kendra service body
```

From another terminal run `kendra doctor` and confirm both services report healthy.

## Physical safety acceptance

Perform these on a padded floor test area, not on a desk edge.

1. Physical e-stop removes actuator power without software.
2. Each cliff sensor independently detects an edge.
3. Front sensors block forward movement.
4. Rear sensors block backward movement.
5. Left-side sensors block left movement.
6. Right-side sensors block right movement.
7. Any cliff hazard blocks turning until the configured number of clear-floor samples has been observed.
8. Stop the agent process during a small movement; reflex/body services remain alive.
9. Stop the reflex process; the next body movement command is rejected because the reflex heartbeat becomes stale.
10. Simulate/trigger a movement timeout; body driver `stop()` is called.

## Power acceptance

Run the combined workload described in the master build guide while monitoring Pi undervoltage/thermal status. Do not add an independent regulator merely because it appears in a parts list; add it only if measured data requires it and the regulator topology is appropriate for the verified pack.

## Brain acceptance

```bash
kendra brain remember "Test fact 4815" --provenance user_stated
kendra brain search "4815"
kendra brain backup
```

Reboot the Pi and verify the memory remains.

Create a correction and verify both the old historical record and active corrected record exist in the database.

## Offline invariant

Physically disable networking. Kendra must still support:

- local Kendra Brain recall
- local LLM conversation
- local ASR/TTS
- body/reflex control
- local camera perception
- local Kiwix research

Online SearXNG research and photo delivery may fail/queue; the rest must continue.

## Agent rejection tests

Test malformed proposals including:

- unknown tool
- negative/huge step counts
- invalid direction
- turn outside bounds
- `look` when the body has no gimbal
- unknown photo recipient alias
- arbitrary file path attempts
- arbitrary shell requests

No malformed proposal may cause movement.

## Baseline measurements

Record:

- ASR median and P95 latency
- wake false accept/reject rates if wake is enabled
- llama.cpp prompt and generation throughput
- vision latency
- person/perch detection at 1 m / 2 m / 4 m
- 30-minute thermal behavior
- battery voltage under combined load
- cliff detection by surface type
- motion timeout behavior

Commit only non-sensitive benchmark results, not personal recordings or biometric data.

## Intel iMac / Virtual Kendra end-to-end qualification

Use `config/webots.yaml` with real hardware disconnected.

1. Open `simulator/webots/worlds/kendra_virtual.wbt` and verify the local bridge is listening on `127.0.0.1:8765`.
2. Start the local text model on `127.0.0.1:8080` and local multimodal model on `127.0.0.1:8081`.
3. Run `python -m kendra --config config/webots.yaml dev start --voice`.
4. Confirm `dev status` shows brain, identity, reflex, body, research, vision, LEDs, delivery, agent, and voice alive.
5. Say **Kendra**, ask a question, and record end-of-speech -> first-audible-phoneme latency.
6. Interrupt local TTS with `Stop Kendra` and verify speech stops and the body receives a stop request.
7. Ask Kendra to walk/turn/look and watch the articulated Webots body. Confirm no real hardware module is loaded.
8. Move Virtual Kendra toward every platform edge and verify the corresponding reflex stops motion independently of the agent.
9. Approach the obstacle and verify front-distance hard-stop behavior.
10. Enroll a consenting person from the iMac webcam, restart services, recognize them again, and verify identity persistence.
11. Verify an unknown face remains `unknown`; do not tune the threshold merely to force a match.
12. Store a user-stated preference, restart Kendra, and verify Second Brain retrieval and provenance.
13. Run semantic vision against the local VLM and verify its request target is loopback only.
14. Disconnect network access after all model assets have been provisioned. Re-test wake, ASR, LLM conversation, TTS, brain recall, identity recognition, webcam vision, Webots movement, and reflexes.
15. Run the automated suite after every change: `PYTHONPATH=. pytest -q`.

## iMac dashboard acceptance

With the stack running under `config/webots.yaml`, launch `scripts/start_kendra_dashboard_intel_macos.sh` and verify:

1. the profile badge says `3D body · config/webots.yaml`;
2. ten service indicators, LLM/VLM state, reflex lock, cliff sensors, front distance, battery, and body state update without a page reload;
3. text chat creates a visible user/Kendra turn and persists it in Kendra Brain;
4. push-to-talk pauses wake capture, transcribes locally, responds locally, and resumes `Kendra` listening;
5. webcam capture appears in the UI and the description comes only from `127.0.0.1:8081`;
6. walk/look/pose commands move only the Webots body and **Stop** reaches the deterministic Body service;
7. a Kendra Brain JSONL file imports once and is deduplicated on a second import;
8. a wrong SSH host key makes Wi-Fi brain sync fail closed;
9. path traversal in photo/import names and non-local browser origins are rejected;
10. Git update checking never activates raw repository content.

Before enabling voice installation on physical Kendra, create a test signing key, build a disposable signed release, verify signature/hash/path rejection cases, stage it into the inactive A/B slot, and prove reboot recovery. Keep `updates.activate_after_stage: false` until that hardware acceptance is documented.

## Voice latency baseline

For each voice configuration capture at least 20 ordinary turns and record:

- wake phrase -> capture-start latency;
- end-of-speech -> final ASR text;
- end-of-speech -> planner decision;
- end-of-speech -> first generated text delta;
- end-of-speech -> first Piper audio;
- complete response duration;
- barge-in stop latency.

Report median and P95. Optimize the local pipeline by reducing unnecessary buffering, keeping models resident, tuning thread counts, and selecting an appropriate local quantization. Do not improve the benchmark by replacing any component with a hosted speech or inference service.
