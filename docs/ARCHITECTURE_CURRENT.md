# Kendra architecture — current truth (2026-08-18)

- brain: Gemma 4 E2B IT Q4_0 via llama.cpp on port 8080; slot 0 = conversation (never evicted), slot 1 = tools; KV files persist under runtime/slots; prewarm during listening; selective thinking budget 128.
- eyes: Moondream2 (Q4 + f16 projector) via llama.cpp on port 8081, warmed at startup; YuNet counts people authoritatively; SFace recognizes enrolled faces; ambient motion-gated observation; curiosity-approach walks toward interesting sights (reflex-gated), checks for obstacles before moving, looks again after moving, and comments aloud on objects and people it finds (politely — never over a conversation).
- ears: Parakeet TDT 0.6B int8 via onnxruntime in-process, ~0.3s per utterance; whisper-server then whisper-cli as fallback; Vosk wake word "Kendra"; wake-tail buffer preserves first words; noise captions filtered.
- voice: Piper en_US-amy-medium, phrase-streamed with affect prosody; synthesized thinking blips while working; echo guards prevent self-repetition.
- memory: SQLite brain with Qwen3-Embedding-0.6B semantic vectors (relevance-gated hybrid search), instant consolidation, research findings cached as memories, idle-time dreaming distills insights, opinions evolve with supersession.
- second brain: Karpathy-pattern file wiki at data/second_brain — every turn, sight observation, and research result appends to an immutable raw/ JSONL log; her own idle agent compiles raw entries into markdown wiki/ concept pages with [[links]] (including kendra-self, where her opinions and feelings accumulate); MANIFEST.md is the deterministic schema; the best wiki excerpt rides every retrieval in ~3 ms; plain files, transplants to the Pi NVMe as-is.
- research: SearXNG (Docker, pinned digest) online with Kiwix offline fallback; brain-cache answers repeats; snippets-first with 4s engine timeouts.
- interaction: voice-first (text chat disabled); turn timings stored as metadata and displayed by the desktop app only.
- body: simulation driver today (Webots profile available); RaspClaws-Metal is the physical target; all motion reflex-gated and fail-closed behind hardware gates.
- target hardware: Raspberry Pi 5 16GB + NVMe + RTC + active cooler; full transplant procedure in docs/TRANSPLANT_GUIDE.md.
