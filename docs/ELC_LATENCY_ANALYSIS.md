# ELC/Ordinary agent-demo analysis — what transfers to Kendra

Analyzed 2026-08-19: /Users/jonathanlupo/Documents/client-elc-ordinary-agentdemo-app.
Honest framing: that app outsources its entire voice loop (ASR/VAD/LLM/TTS)
to Anam AI's cloud over one WebRTC session, and its "memory" and "skin scan"
are scripted client-side theater. The transferable engineering lives in the
Anam SDK's turn-taking design and the app's perceived-latency discipline.

## Already mirrored in Kendra (validation, no action)

- Streamed TTS on first complete phrase (their TalkMessageStream ≈ our
  PhraseAccumulator), system prompt paid once and KV-cached (their
  token-mint prompt ≈ our slot persistence + cache-reuse), instant thinking
  cues, keyword fast-path intent tier (our deterministic routing), warm
  always-open channels (unix sockets, resident servers).

## Adopted from this analysis

1. **Ambient-description reuse for generic sight** (their `addContext`
   out-of-band context injection, localized): her ambient eyes already
   describe the room; a generic "what do you see" within
   `vision.reuse_recent_seconds` (45s) of any description now reuses it —
   no fresh 8-16s Moondream pass on the critical path. Precision questions
   (count/read/holding/color/who) always look fresh; the blind-honesty and
   fabrication gates are untouched.

2. **Recall choke diagnosis, settled by measurement**: retrieval is 10-50ms
   (embedding + SQLite + wiki). ALL recall latency is LLM prefill +
   generation + CPU contention from background LLM work sharing the server.
   Fixes: slot-0 sanctity (done), background work on slot 1 with idle gates
   (done for consolidation/dreams), and the fine-tune charter shrink
   (docs/UNSLOTH_FINETUNING_PLAN.md) which removes ~1,700 prompt tokens.

## Queued (worth doing, not tonight)

- **Interrupted-flag in history** (their correlation-ID barge-in): when
  Jonathan interrupts her mid-reply, store the truncated text with an
  interrupted marker so the model knows he never heard the tail. Matters on
  the robot where barge-in monitoring is on; desktop keeps it off.
- **Slot-store fact table**: typed columns for stable facts (people,
  preferences, appointments) consulted by exact lookup before semantic
  search. Our wiki person pages are halfway there; a `facts(subject, key,
  value)` table would finish it.
- **Perceived-latency budget**: their demo treats 0.6-1.2s as acceptable
  when feedback is continuous — matches our ack + thinking-tone design;
  keep first-audio-out under ~1.5s as the target once the LoRA lands.

## Explicitly not transferable

WebRTC/ICE mechanics (cloud transport), server-side VAD knobs (Anam's),
lip-synced avatar video, and the fake scan/typing theater beyond what our
acks and tones already do honestly.
