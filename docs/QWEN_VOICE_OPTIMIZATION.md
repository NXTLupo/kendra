# Optimizing Qwen for Kendra's voice chat

Source: **Qwen3-Omni Technical Report**, Qwen Team, 2025-09-23
(`research/qwen_expert.pdf`, 25 pages). Read alongside `docs/EDGE_MODEL_PLAN.md`.

## What the paper actually is

Qwen3-Omni is a unified text/image/audio/video model built on a **Thinker–Talker
MoE** architecture, Apache-2.0. Headline result for us: **234 ms theoretical
end-to-end first-packet latency** in cold start.

Its component sizes matter more than its benchmarks:

| Module | Architecture | Params | Streaming |
|---|---|---:|---|
| Audio encoder | AuT | 650M | yes |
| Vision encoder | SigLIP2-So400M | 540M | – |
| Thinker | MoE Transformer | 30B-A3B | – |

**Qwen3-Omni is not deployable on Kendra.** A 30B MoE does not fit a Pi 5, and
its *audio encoder alone* (650M) is the size of Kendra's entire brain. Nothing
here is a drop-in. The value is architectural: it is a detailed description of
how a team that cares about first-packet latency actually gets it, and every
one of their techniques has a cheap analogue Kendra can adopt.

## The five transferable lessons

### 1. Latency is won by streaming from the first frame, not by a faster model

Qwen replaced computationally intensive block-wise diffusion in the waveform
stage with a **lightweight causal ConvNet (Code2Wav)**, explicitly so synthesis
can start from the first codec frame rather than the first complete block. They
report this "significantly reduces inference latency and computational cost
(FLOPs) while achieving superior audio fidelity compared to more complex
DiT-based vocoders."

The general principle: **the cheapest architecture that can start emitting
early beats a better architecture that must finish a block first.**

Kendra already has the right shape — `voice/streaming.py`'s `PhraseAccumulator`
starts Piper on the first complete phrase instead of waiting for the full
response. But her granularity is a *phrase* (`min_phrase_chars: 28`), where
Qwen's is a single codec frame. Lowering `min_phrase_chars` is the single
cheapest latency win available, at the cost of choppier prosody. Measure it.

### 2. Chunked prefilling — encode audio while the person is still talking

Qwen retains **chunked prefilling**: the audio and vision encoders emit chunks
along the temporal dimension, and AuT uses **block-wise window attention to
enable real-time prefill caching**. Transcription work overlaps with speech
instead of following it.

Kendra does the opposite today. `voice/service.py` calls
`audio.capture_utterance()` to completion, writes a WAV, then runs whisper-cli
on the finished file. Every millisecond of ASR is dead air *after* the user
stops talking.

This is Kendra's largest structural latency source, and it is fixable without
changing models: whisper.cpp supports streaming/partial decoding. Overlapping
ASR with capture would remove nearly the whole ASR cost from perceived latency.

**Recommended next latency work, in order:** streaming ASR overlap → lower
phrase granularity → model tuning. Not the reverse.

### 3. Separate the reasoner from the speaker

Thinker produces text; Talker produces speech codecs. They are separate modules
with separate budgets.

Kendra already implements this split in `agent/planner.py`:
`stream_voice_turn()` runs a small schema-constrained planner call
(`temperature=0.1`, `max_tokens=180`) to decide *what to do*, then a second
streaming call generates the spoken prose. Keep this. It is the same idea, and
it is why the planner's JSON latency does not block speech.

### 4. Persona via system prompt is a capability of large models, not a
technique

The abstract claims "strong instruction following" and "fine-grained
customization of conversational tone and persona via user-defined system
prompts." That is true — of a 30B-A3B model.

**Measured on this machine, 2026-08-17**, trying to give Kendra a
partner-not-servant register:

| Attempt | Qwen3-0.6B result |
|---|---|
| Charter rewrite (identity as peer) | still "How can I assist you today?" |
| Explicit negative instructions | no effect; naming the phrase may prime it |
| Exemplars inside the system prompt | no effect |
| Exemplars as prior message-role turns | no effect |
| `repeat_penalty` 1.15, `presence_penalty` 0.6, temp 0.7 | varied wording, same servile register |
| Removing `self_model` from live context | stopped verbatim recitation, register unchanged |

Same prompts, same code path, **Qwen3-4B**: followed the persona immediately —
so faithfully that it reproduced the exemplar text word for word (a separate
hazard; exemplars must now contain no factual claims, or the model asserts
invented events as fact).

**Conclusion: persona adherence is a capacity threshold, and Qwen3-0.6B is
below it.** No amount of prompt engineering closes this. This is the single
most important finding for Kendra's product feel, because "partner, colleague,
friend" is not a nice-to-have — it is the point.

### 5. Thinking mode is a selective tool with a real cost

Qwen ships a separate Thinking model that "explicitly reasons over inputs from
any modality" and gains ~4.4 points on Math/STEM over the Instruct baseline —
paid for in latency. They ship it as a *separate model*, not a mode toggled per
greeting.

For Kendra: keep thinking off by default, and if selective thinking is added,
gate it with `--reasoning-budget N` (see `docs/EDGE_MODEL_PLAN.md`). A robot
that pauses to reason about "hello" is worse than one that never reasons.

## Measured latency reality (Intel iMac, 2026-08-17)

Full agent turn via `AgentClient.turn`, model warm:

| Model | Per turn | Persona held? |
|---|---:|---|
| Qwen3-0.6B Q8_0 | 7–15 s | no |
| Qwen3-4B Q4_K_M | 22–37 s | yes |

Both are far outside the <500 ms first-token target. Note these measure the
**non-streaming** path and include work that is not the LLM: brain retrieval,
`observe_people_each_turn` vision context (2 s timeout), body observation, and
memory consolidation. Before blaming the model, profile the turn. The streamed
`stream_voice_turn` path should be dramatically better on perceived latency
because Piper starts on the first phrase.

**Do this before any further model change:** instrument a turn end to end and
attribute the milliseconds. It is entirely possible the model is a minority of
the wall clock.

## Case study: the BMO Pi build (`research/models.json`)

Transcript of "I made a real BMO local AI agent with a Raspberry Pi and Ollama"
(brenpoly, YouTube l5ggH-YhuAw) — the closest thing to Kendra in the wild: a
Pi 5 embodied agent with wake word → Whisper → local LLM → Piper, camera, and
typed tool use. Their Pi has **16 GB** of RAM; Kendra's has 8, so their
headroom does not transfer 1:1. What their experience confirms and adds:

1. **Their single biggest latency fix was keeping models resident.** Ollama
   was reloading the model on every prompt ("stopping and starting the engine
   of a car every time we need to brake"); a warm-up state that loads
   everything at startup fixed it. Kendra already does this — persistent
   `llama-server` processes — and it stays a hard rule: the model never
   reloads between turns.
2. **They abandoned one multimodal model for a mix of small specialists** —
   text-only Gemma 3 for chat plus **Moondream** for image analysis — because
   the multimodal package was too slow. That validates Kendra's split
   (Qwen3-0.6B text + separate VLM). But their swap-on-demand design paid
   ~1 minute per vision request; Kendra keeps both models resident on
   separate ports, which is the right call.
3. **Moondream is a real candidate for Kendra's Pi vision problem.** The
   open question in `EDGE_MODEL_PLAN.md` is the 2.6 GB Qwen2.5-VL-3B on an
   8 GB Pi. Moondream (~2B, tuned specifically for edge image description)
   is proven working on a Pi 5 in this build. Evaluate it as the 8081
   endpoint's model before any LLM tier change.
4. **Instant feedback clips are how they killed perceived latency** — not a
   faster model. Pre-generated Piper voice clips ("On it", "Checking on
   this") play at every state transition so the robot never feels dead
   while the LLM works. **Implemented for Kendra 2026-08-17**: see
   `kendra/voice/acks.py` — cached Piper clips in her own voice, played the
   instant capture ends, concurrently with ASR + generation. Config:
   `voice.acks`. Same code path on iMac and Pi.
5. **NVMe SSD instead of the SD card** materially improved their model load
   times. Worth adopting for Kendra's Pi build; add to the hardware list.
6. **openwakeword with a custom-trained wake model** worked well for them
   ("Hey Beimo", trained via the openwakeword Colab). Kendra's `wake.py`
   already supports an `openwakeword` provider; training a custom "Kendra"
   model is the upgrade path if the Vosk grammar ever proves too loose or
   too deaf.
7. What **not** to copy: Ollama itself. It pulls weights at runtime by name,
   which breaks Kendra's pinned-SHA-256 provisioning discipline, and it was
   also the source of their reload problem. llama.cpp with pinned GGUFs
   remains correct.

## Second full read: what section 2.4 exposed (2026-08-17, evening)

A page-by-page read of the report (all 25 pages, including the appendix)
surfaced the finding that mattered most: **section 2.4 states that dialogue
speech generation is conditioned on the full conversational history, and that
this is critical.** Kendra had none — `agent/planner.py` generated a fresh
`session_id` per call and sent zero prior turns, so every utterance was
answered cold. That was the actual "this is not a conversation" bug.

Fixes shipped, in order of discovery:

1. **Rolling conversation history.** New brain RPC `recent_turns` (last N
   turns within an age window) injected into every prompt path. Config:
   `agent.history_turns: 6`, `agent.history_max_age_seconds: 900`.
2. **History as a quoted transcript note, not replayed message roles.** Small
   models treat prior assistant turns in the message array as few-shot
   templates and copy their own old replies verbatim. A single system note
   ("Conversation so far...") keeps referents without inviting imitation.
3. **Episode memories excluded from live retrieval** (`exclude_kinds`). Raw
   dialogue episodes fed back through RELEVANT KENDRA BRAIN CONTEXT caused
   verbatim parroting — one bad 0.6B reply was stored once and then repeated
   forever, by every model. Episodes remain available to the recall tool.
4. **Appendix 9.1**: the Thinking variant is *worse* than Instruct on
   ASR/S2TT and music, with a "higher propensity for hallucinations" on
   perception tasks. Hard evidence for keeping `--reasoning off` on Kendra's
   conversational/perception path.
5. Section 2.1's decoupling rationale — external modules (RAG, function
   calling, safety filters) intervene on the Thinker's *text* before the
   Talker speaks, with distinct system prompts for response style vs audio
   style — is exactly Kendra's planner → `speakable()` → Piper-affect chain.
   Keep that boundary.

## Controlled A/B under the fixed pipeline (2026-08-17)

Same four-turn script (greeting → "my favorite planet is Neptune" →
follow-up "why do you think I like it?" → recall "which planet did I just
tell you I love?"), clean brain, identical config, full agent path:

| | Qwen3-0.6B Q8_0 | Qwen3-1.7B Q8_0 |
|---|---|---|
| Latency per turn | 5–7 s | 11–15 s |
| Follow-up ("it" → Neptune) | failed | resolved |
| Recall question | leaked a style exemplar verbatim | recalled Neptune (slightly muddled attribution) |
| Coherence | word salad, identity confusion | consistent, used Jonathan's name |

**Decision: Qwen3-1.7B Q8_0 is Kendra's brain** (swapped in config, scripts,
manifests, and lockfile on 2026-08-17;
`sha256 061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a`).
0.6B is retired to an A/B baseline. Note the official Qwen 1.7B GGUF repo
publishes **only Q8_0** — there is no upstream Q4_K_M; quantize locally from
the official weights if the Pi benchmark demands it.

Also fixed the same evening, because no model can compensate for them: the
OS default audio input was a **USB guitar amplifier** delivering permanent
silence (voice.capture.device is now `auto`: probe every input, pick the one
carrying signal, calibrate the VAD threshold to 4x its ambient floor), and
acknowledgment clips now play only when speech was actually captured.

## Qwen3-TTS repo analysis (2026-08-17, night)

Analyzed https://github.com/QwenLM/Qwen3-TTS for further latency cuts.

**What it is:** six Apache-2.0 TTS models (0.6B and 1.7B; VoiceDesign /
CustomVoice / Base variants plus a 12 Hz, 16-codebook tokenizer), 10
languages, 3-second voice cloning, and a **97 ms end-to-end first-packet**
claim with synthesis starting after a single input character.

**Verdict: do not adopt it as Kendra's TTS.** Three reasons, all measured or
structural:

1. **It has no CPU path.** GPU-first (FlashAttention 2, fp16/bf16, vLLM);
   no GGUF, ONNX, or llama.cpp support. The 97 ms number is a GPU number.
   On Kendra's CPU-only fleet an autoregressive 0.6B TTS transformer would
   compete with the 1.7B brain for the same six cores and would be slower
   than what it replaces.
2. **TTS is not Kendra's bottleneck.** Piper synthesizes a first phrase in
   ~0.55 s; the measured dead air lives in LLM prefill and ASR.
3. **Memory.** Another resident 0.6–1.7B model on a machine that was in
   6.9 GB of swap earlier the same day is how the latency problem started.

**What it still taught — and what shipped from it:** its core design point is
*emit from the first token; never hold finished audio hostage to a buffer*.
Auditing Kendra's `PhraseAccumulator` against that principle found a real
defect: no phrase could be released before `min_phrase_chars` (28), so a
short reply like "Hey. How's it going?" was not spoken until the **entire
generation finished**. Fixed: a completed sentence (`. ! ?`) is now released
to Piper immediately at any length; comma/clause cuts still wait for
`min_phrase_chars` to avoid choppy fragments. Measured result, streamed voice
path, warm: **first spoken phrase at 4.6–5.7 s consistently** (was 12–15 s
before the evening's prompt-tail fix, and worse for short replies).

Revisit Qwen3-TTS only if (a) the robot ever gains a GPU/NPU, or (b) upstream
ships a CPU/GGUF path — its voice cloning would then let Kendra have a truly
custom voice. Park it; the multi-codebook streaming philosophy is the same
one from the Qwen3-Omni paper, and its CPU-appropriate analogues are now
implemented.

## Latency ledger (Intel iMac, 2026-08-17, warm turns)

| Stage of the day | First speech | What changed |
|---|---:|---|
| Morning | 12–22 s + broken dialogue | baseline; ASR broken, no history |
| Cache reuse + stable prefix | ~4.6 s first token (agent path) | `--cache-reuse 256`, prompt reorder |
| 1.7B swap | 11–15 s | dialogue fixed, model 2.7x bigger |
| Swap-pressure fix + lean context | 3.5–4.4 s first token | VLM opt-in, q8_0 KV, 2-memory live context, consolidation deferred |
| Sentence-boundary release | **4.6–5.7 s first spoken phrase** | short sentences speak immediately |

### Roadmap implementation results (2026-08-17, late night)

All three roadmap items were implemented or resolved the same night:

1. **Persistent `whisper-server` — DONE.** New `voice.asr.provider:
   whisper_server` (default) keeps the ASR model resident on
   `127.0.0.1:8082` with automatic `whisper-cli` fallback when the server is
   down. Measured: **0.54–0.77 s** per utterance, was 1.66 s. Binds loopback
   only, zero outbound sockets (verified with lsof); the client refuses
   non-loopback URLs. iMac: `scripts/start_asr_intel_macos.sh`, auto-started
   by the desktop app. Pi: `systemd/kendra-asr.service`, same binary from the
   same whisper.cpp tree.
2. **Locally quantized Q4_K_M brain — DONE, and it is now the runtime
   model.** `llama-bench`: prefill **150.7 vs 71.5 tok/s (2.1x)**, generation
   **19.5 vs 12.4 tok/s (1.6x)**. Dialogue quality held on the 4-turn probe —
   the recall answer opened with the single word "Neptune." The Q4 artifact
   is generated locally by `llama-quantize --allow-requantize` from the
   SHA-pinned Q8_0 (never downloaded); the launcher auto-generates it when
   missing, and `INSTALL_PI.md` documents the same step for the Pi.
3. **Streaming ASR overlap — CLOSED as no longer worth it.** With the server
   resident, ASR is ~0.6 s of a ~3 s turn; overlapping it with capture would
   save well under half a second at the cost of chunked-decode accuracy
   risk. Revisit only if ASR grows (larger Whisper model or long utterances).

**Final measured turn, warm (iMac):** speech ends → ack clip immediate →
ASR 0.65–0.77 s → **first spoken phrase 1.5–3.1 s** → full reply 2.2–5.9 s.
Morning baseline was 12–22 s with broken dialogue.

### Chat-log review round (2026-08-18, after live testing)

Reviewing the real conversation logs surfaced two comprehension bugs that
masqueraded as "she doesn't remember anything", plus the final latency lever:

1. **The VAD was decapitating sentences.** The capture only began once speech
   crossed the energy threshold, so "Ask me a question" arrived as "me a
   question" and "Think you are..." as "Thank you are...". Kendra answered
   the mangled text and looked amnesiac. Fixed with a **0.6 s pre-roll ring
   buffer**: audio from just before the trigger is prepended to the capture.
2. **She was quoting her own history again.** The default llama.cpp penalty
   window (`repeat_last_n` 64) only covers freshly generated tokens, so
   verbatim lines from the history note were free to copy. Now
   `repeat_last_n: 512` covers the history region during generation.
3. **Prewarmed prefill — the chunked-prefill lesson applied for real.** The
   voice service now sends charter + exemplars + history with `max_tokens=1`
   the moment capture begins, concurrently with the user speaking; by the
   time ASR finishes, only retrieved memories and the user's words still need
   prefill. Also fired once at service startup so the first turn of the day
   is warm. Measured: **first spoken phrase 0.6–1.4 s after speech ends**
   (plus ~0.7 s ASR ahead of it). History window widened to 6 turns / 1 hour
   since its prefill cost now overlaps speech.
4. Acknowledgment clips reduced to a single "Hmmm." by request
   (`voice.acks.phrases`).

## Recommendation

The 0.6B-for-latency decision was correct on the axis it was chosen for, and
wrong on an axis nobody measured. Resolve it with evidence, not preference:

1. **Profile the turn first.** If vision context and consolidation dominate,
   fix those and 0.6B may be fine.
2. **Test Qwen3-1.7B Q4_K_M** as the middle tier. It is the obvious candidate
   between "cannot hold a persona" and "22–37 s per turn", and it is not yet
   downloaded. Add it to `fetch_local_models.py` with a pinned SHA-256 and
   measure both persona adherence and latency on the Pi, not just the iMac.
3. **Implement streaming ASR overlap** (lesson 2) regardless of model choice.
   It is the biggest perceived-latency win available and is model-independent.
4. Keep Qwen3-4B where it already is: opt-in, desktop-only, never on the Pi.

An 8 GB Pi 5 can hold a 1.7B Q4_K_M model comfortably. The open question is
tokens/sec on a Cortex-A76, which only `llama-bench` on the actual Pi can
answer. Do not settle this argument on the iMac.


## Intelligence & agency round (2026-08-18)

Sources: Qwen3 Technical Report (`research/qwen3.pdf`), official + unsloth
Qwen3-0.6B model cards, LessWrong "Self-recognition finetuning..." post,
arXiv 2607.28607 (consciousness-vector steering).

1. **Recall-killing sampler bug found and fixed.** `repeat_last_n: 512` with
   stacked penalties punished every token already in the prompt — including
   names Kendra was asked to RECALL. Symptoms: word salad + apparent amnesia.
   Now per the official card: temp 0.7 / top_p 0.8 / top_k 20 / min_p 0,
   presence_penalty 1.0 only, repeat window back to 64 (generated text only).
2. **Selective thinking (Qwen3's budgeted hybrid).** Server runs
   `--reasoning auto --reasoning-budget 512`; ordinary turns send
   `enable_thinking:false` per request; the complexity router enables it for
   analytical questions (official thinking sampling: temp 0.6 / top_p 0.95).
   Verified: multi-step gait explanation, coherent, ~55 s when asked.
3. **Instant memory**: consolidation delay 1 s, serialized, never canceled.
   Verified: two facts recalled correctly seconds after being stated.
4. **Live internet research verified end to end**: SearXNG (Docker, image
   pinned by digest in the lockfile, secret in gitignored `searxng/.env`) →
   real pages fetched → grounded answer with `research` in the tool trace.
   Offline the same tool auto-falls back to Kiwix — Pi-offline safe. Research
   trigger keywords broadened (news/latest/today/online/look it up/etc.).
   Context economics fixed: 6144-token slots (q8_0 KV), tool results capped
   at 6000 chars, research sources 6000 chars x3.
5. **Identity anchor (LessWrong)**: consistent self-identity in the stable
   prompt prefix; charter v2 gives Kendra explicit agency, curiosity,
   evolving opinions, and self-recognition of her own labeled words.
   `kendra_opinion` memories persist and supersede older opinions per topic.
   The deterministic safety cage (reflex/tool whitelist/gates) is untouched —
   it protects physical safety, not personality.
6. **Control vectors (arXiv steering analogue)**: `llama-cvector-generator`
   is built; generating a Kendra agency vector from contrastive prompts is
   the next experiment (llama.cpp `--control-vector-scaled`, ~zero per-token
   cost). Not yet shipped.
7. **Ambient thinking sounds**: numpy-synthesized warm blips (no assets,
   sounddevice, Pi-identical), start 0.9 s into thinking, stop at first
   speech. `voice.thinking_sounds`.
