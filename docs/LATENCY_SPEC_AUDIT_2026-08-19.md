# Latency spec audit — status of all 13 recommendations

Source: research/KENDRA_LATENCY_OPTIMIZATION_RESEARCH_AND_IMPLEMENTATION_SPEC_2026-08-19.md
Audited against the live stack, evening 2026-08-19. Measured state at audit
time: burst conversation avg 7.8s / worst 9.7s; recall ~8s; movement 0.1-2.4s;
who-questions 0.6-0.8s; diagnostics 0.0-0.3s; first-audio 0.5s best.

| # | Recommendation | Status | Evidence |
|---|---|---|---|
| 1 | Instrument every stage | DONE | turn timings in metadata (kind/search_s/sight_s/total_s), "First audio out in Xs" log, llama print_timing correlation |
| 2 | Hard-disable thinking for ordinary turns | DONE+ | enable_thinking off for chat; trigger narrowed today — "thinking about" false-positive cost 30.6s smalltalk; analytical asks still think |
| 3 | Eliminate planner pass for ordinary chat | DONE | deterministic bypasses: chat, movement, diagnostics, who, recall, research, sight, mic-checks; planner only for genuine tool ambiguity |
| 4 | Dynamic low-latency endpointing | **DONE TODAY (the new clue)** | trailing silence 0.8s fixed → 0.45s once >1.2s of speech captured; short fragments keep 0.8s |
| 5 | Everything resident | DONE | mlock on both llama servers, embedding warm at brain boot, VLM 64px warmup, persistent httpx clients, pre-rendered ack WAVs |
| 6 | Persistent in-process TTS | DONE | PiperTTS/KokoroTTS persistent objects; no subprocess per phrase; fixed lines play from WAV cache in ms |
| 7 | TTS on short safe clauses | DONE | PhraseAccumulator min 28 chars ≈ the ST-paper Pareto point |
| 8 | True cancellation everywhere | MOSTLY | barge-in kills TTS+generation; stop preempts walks in 0.05s via navigation epoch; NOT yet: cancelling in-flight research/VLM on barge-in (queued) |
| 9 | Stable prompt prefix + reuse | DONE | byte-identical charter+exemplar prefix, --cache-reuse 256, slot 0 never shared (vision questions moved to slot 1), slot save/restore |
| 10 | VLM out of the conversational path | DONE | sight only on sight intent; ambient yields to conversation (90s window); reuse-recent description (45s) answers generic sight in 0.3s |
| 11 | Camera ring buffer + deterministic lane | PARTIAL | renderer streams a frame every 5s (depth-1 buffer); deterministic lane = YuNet faces + motion diff; a true ring with history is Pi-phase (picamera2) |
| 12 | Fast Lookup vs Deep Research split | DONE (implicit) | snippets-first short-circuit answers from search snippets when dense enough; page fetches parallel and capped; news via Google News RSS |
| 13 | Model A/B only after architecture fixes | DONE | Qwen-LoRA A/B measured and rolled back on evidence; scaled-LoRA architecture retained on disk; LFM2 benched |

## Also fixed today under this goal's umbrella
- Guard-cascade amplification (up to 4 prefix-busting regens/turn → one
  shared budget, warm-prefix regen prompts) — this was the "she died again".
- Consolidation bursts (one LLM call per turn → coalesced batch extraction;
  phatic turns never queue) — this was the intermittent 30-90s spike class.
- Register score counts self-descriptions only, so her intellectual musing
  is never punished with a regeneration.

## Open items carried forward
- Research/VLM cancellation on barge-in (rec #8 completion).
- True camera ring buffer on the Pi (rec #11, picamera2 native).
- Moonshine manifest contradiction: resolved in practice — ASR is Parakeet
  ONNX with whisper fallback; manifests updated by the architecture sync.
