# Kendra Transplant Guide — moving her mind into the RaspClaws-Metal body

Authoritative as of 2026-08-18. Supersedes the model/software sections of
KENDRA_BUILD_GUIDE_V3.md (whose hardware gates A1–A9 remain the law for the
physical build). Kendra herself carries a summary of this plan in her brain
(seeded by `scripts/sync_architecture_memory.py`) so she can walk Jonathan
through the build by voice.

## 1. Her mind today (what is being transplanted)

| Organ | Model / engine | Resident | Serves |
|---|---|---:|---|
| Language brain | Gemma 4 E2B IT Q4_0 · llama.cpp (slots 0/1 pinned, KV persistence, prewarm) | ~2.8 GB | conversation, planner, consolidation, dreams |
| Eyes (semantic) | Moondream2 Q4 + f16 projector · llama.cpp | ~2.0 GB | scene description, precision looks, ambient vision |
| Eyes (faces) | YuNet + SFace · OpenCV | ~0.05 GB | people counting (authoritative), recognition |
| Ears | Parakeet TDT 0.6B int8 · onnxruntime in-process | ~0.7 GB | 0.3 s transcription (whisper fallback chain) |
| Wake | Vosk small en-US | ~0.1 GB | "Kendra" + spoken stop |
| Voice | Piper en_US-amy-medium (+ synthesized thinking blips) | ~0.1 GB | phrase-streamed speech, affect prosody |
| Memory | Qwen3-Embedding-0.6B int8 · onnxruntime (q4 on Pi) | ~0.65 GB | semantic recall, relevance-gated hybrid search |
| Services | ten Python services + SQLite brain | ~1.5 GB | everything above, reflex-gated |

**Measured total ≈ 7.9 GB resident.** This is the headline spec change:

> **The 8 GB Pi 5 no longer fits her mind with headroom. Buy the 16 GB Pi 5.**
> (Fallback if 8 GB is already owned: Qwen-embedding q4 variant, VLM ctx 2048,
> zram — workable but at the exact memory edge that caused every "she's
> stuck" episode on the iMac. 16 GB is the correct purchase.)

Behavioral systems that transplant unchanged (same code, no flags): ambient
motion-gated vision, curiosity-approach (sight→walk, reflex-gated), dreaming
(idle memory review), brain-cached research (SearXNG native container +
Kiwix offline fallback), voice-first interaction (there is no keyboard),
turn-timing metadata (the desktop app renders it; the robot just stores it).

## 2. Updated bill of materials (changes from V3 marked ★)

| Item | Qty | Planning price | Note |
|---|---:|---:|---|
| Adeept RaspClaws-Metal kit | 1 | $99.99 | chassis, Robot HAT V3.3, 17× AD002 servos, camera, ultrasonic, MPU6050, OLED, WS2812 |
| ★ Raspberry Pi 5 **16 GB** | 1 | ~$120 | her mind measures ~7.9 GB resident |
| Official Pi 5 Active Cooler | 1 | $10.95 | REQUIRED — A76 throttles at 80 °C under sustained inference |
| ★ NVMe M.2 HAT + 256 GB NVMe SSD | 1 | ~$45 | models + brain on NVMe; microSD only boots. Never swap to SD |
| ★ RTC coin cell (CR2032, Pi 5 socket) | 1 | ~$5 | offline she must still know the time (her clock feeds every prompt) |
| 64 GB A2 microSD | 1 | ~$12 | boot media |
| Pololu #2579 IR proximity sensor | 4 | $39.80 | cliff sensing (reflex layer) |
| MCP23017 I2C breakout | 1 | ~$8 | dedicated safety I/O |
| NC e-stop / kill switch | 1 | ~$10 | physical hard stop — the primary stop, always |
| Inline fuse holder + fuses | 1 | ~$5 | battery fault protection |
| reSpeaker XVF3800 USB 4-mic array | 1 | $59.99 | far-field ears (auto-probe prefers it; falls back gracefully) |
| Compact USB speaker | 1 | ~$12.50 | her voice |
| Battery solution per Battery Gate | 1 | ~$20–35 | do not improvise |
| Panel USB extension + SD reader | 1 | ~$10 | offline updates |
| Wire/heat-shrink/fasteners | 1 set | ~$10 | integration |

**Planning total ≈ $470–490.** (16 GB Pi + NVMe + RTC replace the old 8 GB +
256 GB-SD line items at nearly identical cost.)

## 3. Transplant procedure (step by step)

### Phase T0 — bench brain, before any chassis work
1. Flash Raspberry Pi OS **Lite 64-bit** to the microSD; boot; attach NVMe;
   move root to NVMe (`sudo raspi-config` → boot order) per the NVMe HAT doc.
2. Fit the RTC battery; verify `timedatectl` keeps time across a powered-off
   hour with no network.
3. zram only, never SD/NVMe swap files:
   `apt install zram-tools; echo -e "ALGO=zstd\nPERCENT=25" > /etc/default/zramswap`.
4. Clone the repo to `/opt/kendra/current`; run `scripts/install_pi_packages.sh`;
   build llama.cpp (`llama-server`, `llama-quantize`) and whisper.cpp
   (`whisper-cli whisper-server`) per INSTALL_PI.md §2–3.
5. Fetch every pinned model: `.venv/bin/python scripts/fetch_local_models.py --core`
   (brain, mmproj, whisper, piper, vosk, faces, both embedding models,
   Moondream pair, Parakeet trio). Quantize local artifacts:
   Moondream Q4 (start script does it on first run).
6. Install ALL systemd units from `systemd/`: kendra-llm, **kendra-vlm**,
   **kendra-asr**, and the ten service units. Note the OOM ladder inside them:
   vision dies first, then speech, reflex never (OOMScoreAdjust -900).
7. `kendra --config /etc/kendra/production.yaml init && doctor` → must be
   green except hardware gates. Talk to her on the bench: wake word → ears →
   brain → voice must all work headless with Wi-Fi OFF (research degrades to
   Kiwix; everything else is fully offline).
8. Transfer her memories from the iMac: dashboard → "Retrieve memories now"
   reversed, or `brain export-jsonl` on the iMac → import on the Pi
   (biometric identity DB never transfers by design; re-enroll faces).
   Copy `data/second_brain/` verbatim (rsync) — her wiki, raw experience
   log, and manifest are plain files and carry over unchanged.

### Phase T1 — the body (defer to V3 hardware gates, unchanged law)
9. Build the chassis per the vendor manual, then run gates **A1–A9** from
   KENDRA_BUILD_GUIDE_V3.md verbatim: package hash, the 17-vs-16 servo
   accounting, servo mapping, battery path, e-stop, cliff array, calibration.
   `project.mode: hardware` stays fail-closed until every gate passes.
10. Wire the four cliff sensors through the MCP23017 (reflex service config
    `reflex.sensors.provider: mcp23017` matches the default map).
11. Camera: `vision.camera_provider: picamera2`. The renderer eye-stream
    does not exist here; her camera IS her eye, ambient vision and
    curiosity-approach work unchanged (reflex-gated).

### Phase T2 — first embodied session
12. Power on with e-stop armed and legs off the ground (V3 stand). Verify
    reflex heartbeat stops motion when the reflex service is killed.
13. Enable `autonomy.enabled` + curiosity-approach last, after gates pass.
    Her first self-directed act should be the same one rehearsed in the
    simulator: notice something, take two slow steps toward it.

## 4. Keeping her self-aware of this plan

- `docs/ARCHITECTURE_CURRENT.md` is the compact truth of her organs.
- `scripts/sync_architecture_memory.py` upserts that file into her brain
  (subject "architecture", superseding stale entries) plus the build-plan
  summary. **Run it after every architecture change** — it is what lets her
  discuss her own build honestly. The doctor warns if the file is newer than
  the last sync.
