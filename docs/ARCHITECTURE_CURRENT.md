# Kendra architecture — current truth (2026-08-18)

- brain: Qwen3-1.7B Q4_K_M kendra-voice-v1 (personality fine-tuned via LoRA, 2026-08-19; Gemma 4 E2B is the rollback) 
- eyes: Moondream2 (Q4 + f16 projector) via llama.cpp on port 8081, warmed at startup; YuNet counts people authoritatively; SFace recognizes enrolled faces; ambient motion-gated observation; curiosity-approach walks toward interesting sights (reflex-gated), checks for obstacles before moving, looks again after moving, and comments aloud on objects and people it finds (politely — never over a conversation).
- ears: Parakeet TDT 0.6B int8 via onnxruntime in-process, ~0.3s per utterance; whisper-server then whisper-cli as fallback; Vosk wake word "Kendra"; wake-tail buffer preserves first words; noise captions filtered.
- voice: Piper en_US-amy-medium, phrase-streamed with affect prosody; synthesized thinking blips while working; echo guards prevent self-repetition.
- memory: SQLite brain with Qwen3-Embedding-0.6B semantic vectors (relevance-gated hybrid search), instant consolidation, research findings cached as memories, idle-time dreaming distills insights, opinions evolve with supersession.
- second brain: Karpathy-pattern file wiki at data/second_brain — every turn, sight observation, and research result appends to an immutable raw/ JSONL log; her own idle agent compiles raw entries into markdown wiki/ concept pages with [[links]] (including kendra-self, where her opinions and feelings accumulate); MANIFEST.md is the deterministic schema; the best wiki excerpt rides every retrieval in ~3 ms; plain files, transplants to the Pi NVMe as-is.
- research: SearXNG (Docker, pinned digest) online with Kiwix offline fallback; brain-cache answers repeats; snippets-first with 4s engine timeouts.
- interaction: voice-first (text chat disabled); turn timings stored as metadata and displayed by the desktop app only.
- body: simulation driver today (Webots profile available); RaspClaws-Metal is the physical target; all motion reflex-gated and fail-closed behind hardware gates.
- target hardware: Raspberry Pi 5 16GB + NVMe + RTC + active cooler; full transplant procedure in docs/TRANSPLANT_GUIDE.md.

- locomotion: spoken commands (come here / go away / back up / go to X /
  turn left|right|around / go forward / stop, distances in feet, inches,
  metres) parse deterministically to typed MovementIntents — no LLM in the
  motion loop. She announces before moving and reports on arrival. The gait
  model (kendra/body/locomotion.py) carries the vendor's real constants:
  4-phase tripod, 0.4 s per cycle, hip stroke 27.4 deg, knee lift 90 counts;
  Virtual Kendra walks with that same timing and tracks true pose, so the
  simulator and the robot agree. Walks run as bounded segments with reflex
  and front-clearance re-checked between each, pausing when her legs need
  rest. Distance-per-cycle is ESTIMATED until measured on the real robot.
- lights: thinking pulses match her thinking tones — cyan breathing while
  composing, blue chase while researching, green ticks while looking
  (WS2812, 16 px, GPIO12 on the robot; simulated on the desktop).
- hardware bridge: hardware/vendor/kendra_adeept_bridge.py speaks the exact
  RaspClaws signals (PCA9685 0x40 @50 Hz, hip=even/knee=odd, vendor leg map
  with the reversed right side) and is fail-closed: no gates, no motion;
  servos clamp to 520; stop() holds torque rather than collapsing her.
