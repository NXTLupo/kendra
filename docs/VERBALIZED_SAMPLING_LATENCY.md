# Verbalized sampling + reinforcement inference: the latency campaign

Method, applied explicitly: for each paper in `research/`, verbalize candidate
optimizations with predicted gains; treat measurements as the reward signal;
keep what measures well, reject what doesn't, and record both. Baselines and
results are from identical probes on the live stack (2026-08-18).

## The reward signal (measured trajectory)

| Path | Session start | Before this campaign | After |
|---|---:|---:|---:|
| Sight turn | 107 s (cold cascade) | 26 s | **8-9 s warm** |
| Memory question | lazy-load stalls ("forever") | — | **6 s** |
| Research turn | 252 s (worst) | 13-19 s | **13-15 s cold, 7 s brain-cached** |

## Candidates, verbalized per source paper

### From profiling (reward signal collection — precedes all sampling)
- **P0. Stage-by-stage profile of a sight turn** → found the true cause:
  Moondream's ~40 s first-request graph compile after any restart, which also
  starved Gemma prefill to 63 s first-token. Every candidate below was scored
  against this profile, not against intuition.

### From Qwen3-Omni (qwen_expert.pdf)
- **C1. Chunked-prefill analogue — prewarm slot 0 DURING the Moondream look.**
  Prediction: 1-3 s. Measured: sight 9 s → 8 s. KEPT (cheap, correct, stacks).
- **C-rej. Stream the VLM description into Gemma's prefill token-by-token.**
  Rejected in sampling: coordination complexity for <1 s over C1.
- (Earlier keeps from the same paper: prewarm-during-capture, phrase-streamed
  TTS, history conditioning — all still load-bearing.)

### From Penguin-VL (vision.pdf)
- **C2. TRA token-budget discipline on describe generation: 100 → 60 tokens
  (90 for precision looks).** Prediction: ~2.3 s at Moondream's ~17 tok/s.
  Measured: contributes to the 8-9 s sight floor with intact descriptions.
  KEPT.
- (Earlier keeps: motion-gated ambient vision, 448/896 dual-resolution.)

### From Libra (LLM_Research.pdf)
- **C4. Warm every model at startup, never on a user's turn** — Libra's
  "contexts must be persistent" generalized to model state: VLM 64px warmup
  at vision-service boot, embedding-session warmup at brain boot. Measured:
  sight 107 s cascade eliminated; memory first-search stall eliminated (6 s
  turns). KEPT — now recorded as project law.
- (Earlier keeps: slot ownership + KV persistence. Earlier reject: chunk-wise
  KV compression, engine-level.)

### From the Qwen3 card/report (qwen3.pdf)
- Already fully exploited in prior rounds (official sampling, budgeted
  thinking); no new latency candidates found on re-review.

### From the BMO transcript (models.json)
- Already fully exploited (models resident, thinking sounds); re-review
  yielded no new candidates.

### Engineering candidates surfaced during sampling (no single paper)
- **C5. Recall bypass** (memory questions skip the planner): measured 6 s
  with correct recall. KEPT.
- **C6. Parallelize sight + memory retrieval; skip the duplicate
  identity-vision pass on sight turns.** KEPT.
- **C3. Research answer budget 160 → 120 tokens.** Prediction ~3 s; the edit
  failed to match its site AND the projected gain sat inside measurement
  noise (13 vs 15 s across runs) — ABANDONED, recorded as not worth carrying.

## Residual honest floor

Warm sight ≈ 8-9 s = Moondream encode (~3-4 s) + describe (~3.5 s at 60
tokens) + answer generation. Cold research ≈ 13-15 s = search (2-6 s) +
prefill + extraction answer. Below these floors the levers are hardware
(Pi AI HAT, GPU delegation) or model swaps (Qwen3.5-2B eyes A/B — queued),
not orchestration.
