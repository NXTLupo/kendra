# Post-mortem: how dumb Kendra had become, and why

Analysis of Jonathan's live voice/chat transcripts from 2026-08-17/18, as
demanded: every observed stupidity, quoted, root-caused, fixed, and verified.
This document is the permanent record so no future rebuild reintroduces these.

## The observed stupidities, verbatim

| # | What she did (quoted from live transcripts) | How dumb it looked |
|---|---|---|
| 1 | "I can see you. I'm here, right here. What do you think?" — repeated to every sight question | Claimed sight with no eyes; identical canned line every time |
| 2 | "I see you're trying to get a better sense of how you're feeling..." — to "tell me what I'm wearing" | Answered a sight question blind, about the wrong subject |
| 3 | Answered "(upbeat music)" as if Jonathan had said it | Held conversations with his guitar |
| 4 | "Let's see." → recited her own previous music musings verbatim | Parroted herself instead of thinking |
| 5 | "Thank you are living in a simulation" heard for "Think you are..." | Beheaded/mangled hearing made every answer nonsense |
| 6 | "I see a room with three people in it" — Jonathan alone with guitars | Counted guitars as people |
| 7 | Forgot facts stated minutes earlier | 21 turns produced 5 junk memories |
| 8 | 60–250 s of thinking blips before any word | "Endless thinking loop" |
| 9 | "How can I assist you today? 😊" | Service-desk persona instead of a companion |

## Root causes (none of them were "the model is dumb" alone)

1. **No eyes on macOS** — the webcam belongs to the desktop app, never to a
   headless service; sight questions fell to the plain chat path and the
   model invented sight. *Fix:* renderer eye-stream → `vision_frame` →
   `submit_frame` cache; the Pi uses its own camera through the identical
   service. Deterministic `_look_now` injects the scene as ground truth;
   honesty rule when eyes fail.
2. **Sight-question routing gaps** — "what I'm wearing", "look and tell me"
   missed the keyword list (partly due to #5's beheading). *Fix:* sight-intent
   regex + planner bypass for pure sight questions.
3. **Whisper captions non-speech** — "(upbeat music)". *Fix:*
   `_is_noise_caption` filter; those are never turns.
4. **Self-echo** — history replay makes a small model copy its own replies;
   sampler-level fixes wide enough to stop it destroyed recall instead
   (repeat_last_n 512 penalized the very names she was asked to remember).
   *Fix:* sentence-level `_dedup_reply` code guard + regeneration.
5. **Audio decapitation** — VAD threshold starts plus the wake→capture stream
   gap ate first words. *Fix:* 0.6 s pre-roll + 1.2 s wake-tail seeding.
6. **HOG pedestrian detector** — fires on tall shapes (guitars). *Fix:* YuNet
   face counting, marked authoritative over VLM impressions.
7. **Memory pipeline defeated itself** — consolidation canceled on every new
   turn (active conversation never learned anything), hashing embeddings made
   semantic recall noise, retrieval starved by latency work. *Fix:* queued
   never-canceled consolidation (1 s), MiniLM ONNX vectors, restored budgets,
   observations stored as memories, opinions as `kendra_opinion`.
8. **A 3.5× brain tax nobody could see** — `--cache-type q8_0` without flash
   attention cut prefill from 103 to 28 tok/s on this CPU; with three prompt
   prefixes fighting two slots, every turn re-paid full prefill. *Fix:* f16
   KV restored, ctx 12288, prefixes stabilized, consolidation waits for idle.
9. **Persona is a capacity threshold** — Qwen3-0.6B could not hold the
   partner register at all; Qwen3-1.7B holds it with the identity-anchor
   charter. Servile-template turns were purged so history stopped teaching
   her to be a butler.

## Verified end-state (live, this machine)

- Sight: "I see a small, light blue toy spider with big eyes..." — accurate,
  27 s total, via the exact renderer-frame pipeline; Moondream warm sight 4 s.
- Seeing becomes memory instantly: `I saw: KENDRA SPIDER` retrievable
  semantically seconds later.
- Typed chat: 13.1 s through the exact UI protocol.
- Repeat-question test: two distinct answers (similarity 0.42).
- Caption filter: 6/6; sight-intent patterns: 6/6.
- Thinking sounds loop until first speech, hard-capped at 90 s.

## Rules this post-mortem burns into the project

1. Measure on a calm machine; two major wrong verdicts (Moondream "too slow",
   "swap is the whole problem") came from benchmarking during thrash.
2. Purge patterns must match loosely; "I can see you." missed "I can see you,".
3. Test turns contaminate her brain — probe sources must be quarantined and
   purged after.
4. Every latency flag is a suspect: KV quantization, penalty windows, and
   context cuts each silently destroyed something that mattered more.
5. The Pi inherits all of this through shared code and systemd units — the
   iMac is the hard mode, and these fixes are the transplant's insurance.
