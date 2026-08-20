# Optimization pass — 2026-08-20

Twelve targeted optimizations, each implemented, already-present, or
rejected with a measurement. Nothing here was adopted on theory.

Two items assumed infrastructure that no longer exists: research was moved
off SearXNG/Docker earlier today (the Pi will have no Docker), so those
became the equivalent wins on the native HTTP path.

## 1. Voice latency

| Item | Status | Evidence |
|---|---|---|
| **Sub-sentence TTS** | **DONE** | The first phrase may now be short (14 chars) while later phrases keep their length for prosody: `"Oh, absolutely."` starts her talking roughly a second sooner instead of waiting for 28+ characters. Time-to-first-audio is the felt latency; every later phrase hides behind speech already playing. |
| **ASR streaming** | **PARTIAL / correctly shaped** | Parakeet TDT via onnx-asr transcribes a completed buffer — it exposes no streaming API, so chunk-streaming would mean a different ASR. The wake-tail is *not* a delay: it is a 1.2 s ring buffer already captured, prepended so her first words are not clipped. Removing it re-breaks "Ask me a question" → "me a question". No change; documented so it is not retried blindly. |
| **Context pre-warming** | **ALREADY PRESENT** | Slot 0 is prewarmed with the full stable prefix (charter + exemplars + history) *during capture*, before ASR finishes. Feeding partial ASR deltas would require streaming ASR (above); the prefix is the expensive part and it is already paid in parallel. |

## 2. Agent intelligence

| Item | Status | Evidence |
|---|---|---|
| **Memory indexing (dreaming → wiki)** | **DONE — and it was badly behind** | The compile existed but was gated on 15 idle minutes, which never arrives on a talkative day: **621 raw entries were sitting uncompiled** — things she had lived but could not look up. Now it drains in bounded batches after 3 quiet minutes, up to 3 compiles per lull, aborting the moment he speaks again. |
| **Speculative decoding** | **REJECTED — measured** | Qwen3-0.6B Q8 draft for the 1.7B brain (same tokenizer family, the hard requirement). Measured on the real 6-core iMac over 157 generated tokens: **16.4 tok/s with draft vs 17.0 without (−3%)**. On a GPU the draft is nearly free; on this CPU it competes for the same cores. Harness kept at `scripts/bench_speculative_decoding.py` — worth re-running on the Pi, where the ratio may differ. |
| **Dynamic steering per slot** | **NOT POSSIBLE as specified** | llama.cpp control vectors are applied **server-wide**, not per request or per slot, so the planner (slot 1) cannot get a different scale from conversation (slot 0) on one server. Options if tool JSON ever degrades: run the vector at 0 and rely on the personality LoRA, or host a second unsteered server (~1.1 GB more RAM — rejected for the Pi). Current tool-call reliability is fine at scale 2.0, so no action. |

## 3. Research speed

| Item | Status | Evidence |
|---|---|---|
| **Background news caching** | **DONE** | Google News top stories are refreshed on a 6-minute timer and served from memory. Measured: **3.88 s → 1.21 s** for "what's in the news today". Falls back to a live fetch when the cache is cold or stale. |
| **Concurrent fetching** | **ALREADY PRESENT** | Page fetches already run concurrently (`asyncio.gather` over candidates) — they were serialised once and dominated research latency at up to 12 s each. |
| **Streaming parse + Kiwix stall fallback** | **PARTIAL** | Snippets are parsed as soon as the response lands, and Kiwix is already the offline fallback. True incremental parse-during-stream would save well under a second against an 8 s request timeout — below the noise floor of everything else in a research turn. Not worth the failure modes; recorded rather than done. |

## 4. Visual intelligence

| Item | Status | Evidence |
|---|---|---|
| **Temporal tracking / scene cache** | **DONE — biggest win of the pass** | The old cache keyed on a hash of raw JPEG bytes, which *never* repeats with a live camera (a person breathing changes every pixel), so every repeat look paid full price. Now a 16×9 grayscale fingerprint is compared by mean absolute difference: same room, same question, no real movement → instant reuse. Measured on three consecutive looks: **56.1 s (cold) → 12.3 s (warm) → 0.3 s (cached)**. |
| **Thread isolation** | **ALREADY PRESENT** | YuNet/SFace already run through `asyncio.to_thread`, so face work never blocks the event loop or the Moondream call. Verified at every call site. |
| **Face-crop before the projector** | **NOT DONE — deliberately** | Cropping to the face box would raise effective resolution on a person, but her sight questions are mostly about what is *around* the person ("what am I holding", "how many fingers") and the crop would discard exactly that. The existing dual-resolution path (448 px scenes / 896 px precision questions) already targets the same cost, and sight regressions have been the single most painful class of bug in this project. Revisit only with a measured A/B on real questions. |

## Net effect

- repeat look in a still room: **12.3 s → 0.3 s**
- general news question: **3.9 s → 1.2 s**
- first audio: earlier by roughly the length of one short clause
- wiki backlog: 621 entries draining instead of stuck
- speculative decoding: rejected on measurement, harness retained for the Pi

---

## Follow-up: Moondream 0.5B evaluated (2026-08-20)

**Question: will it run on a Raspberry Pi 5? Yes.** Core Electronics'
guide is itself a Pi 5 guide using these exact `.mf.gz` files, and the
`moondream` package runs on **onnxruntime** — already Kendra's runtime for
Parakeet, Kokoro and her embeddings on both x86_64 macOS and aarch64 Linux.
Their Pi 5 figures: **0.5B ≈ 8 s/frame, 2B ≈ 20 s/frame**.

Measured here on a real camera frame (0.5B int8, 693 MB, `models/moondream-05b/`):

| Step | Time | Notes |
|---|---:|---|
| model load | 3.1 s | once at startup |
| **encode image** | **6.9 s** | **once per frame** |
| query "what is the person holding?" | 1.2 s | "a guitar in their hands" — correct |
| query "what is the person wearing?" | 0.9 s | "a grey shirt and glasses" — correct |
| query "how many people?" | 0.7 s | said two; there was one — YuNet stays authoritative for counting |
| free-form "describe this image" | 12.5 s | invented a beard and a black vest — **avoid long descriptions** |

**The architectural prize is encode-once-ask-many.** Today every question
costs a fresh ~12 s look through llama.cpp, and — as this session proved —
that path cannot answer questions at all (`--no-jinja` returns captions,
"Yes", or "ASSISTANT"). With 0.5B, one 6.9 s encode then three targeted
questions at ~1 s each gives richer, more accurate sight in roughly the
time one blind caption costs now.

**Not adopted yet, deliberately.** It is a new inference path (Moondream's
own runtime rather than llama.cpp) and deserves a clean session, not a live
conversation. Install note: it downgrades `huggingface-hub` and `tokenizers`
below what `transformers` wants — verified harmless because her runtime uses
`onnx-asr` and ONNX embeddings, with all ten services healthy afterwards.

Adoption sketch when the time comes: a `moondream_onnx` semantic provider
alongside the llama.cpp one, encode cached per frame with the existing
perceptual scene signature, targeted questions per user intent, YuNet still
authoritative for people counts, and Piper-style rollback via one config key.
