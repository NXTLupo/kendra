# Kendra's edge model plan

Kendra must run her whole mind on whatever machine her body is attached to. The
Intel iMac is the development twin; the Raspberry Pi 5 is the robot's computer.
Neither is ever a client of the other. The Mac is not an inference server, and
`kendra-llm.service` on the Pi runs its own local `llama-server` on
`127.0.0.1:8080`. The only Mac↔Pi traffic that exists at all is optional and
one-directional: a Second Brain JSONL export over SSH, and signed intelligence
releases over Git. Both are file transfers. Kill the network and Kendra keeps
thinking.

## Target machines

| | Intel iMac (2019 27") | Raspberry Pi 5 |
|---|---|---|
| CPU | 6-core Core i5, up to 4.6 GHz | 4-core Cortex-A76 @ 2.4 GHz |
| RAM | 16 GB | 8 GB |
| GPU | Radeon Pro 580X, 8 GB | none usable |
| Build | `GGML_METAL=OFF`, `GGML_BLAS=ON` | OpenBLAS |

The Pi is the constraint. Anything that does not leave headroom on the Pi does
not ship, however comfortable it feels on the iMac.

Do not plan around the Radeon 580X. `scripts/bootstrap_intel_macos.sh` already
builds llama.cpp with Metal disabled, which is the correct call for an
Intel-era AMD Mac. Benchmark CPU first. At 0.6B the GPU question is not worth
the risk.

## Decision: Qwen3-0.6B is the always-on brain

This is already the shipped configuration, not a proposal:

| Where | Value |
|---|---|
| `config/default.yaml` | `llm.model: qwen3-0.6b-q8_0` |
| `scripts/start_llm_intel_macos.sh` | `models/qwen3-0.6b/Qwen3-0.6B-Q8_0.gguf` |
| `config/kendra.env.example` | `KENDRA_LLM_MODEL=/var/lib/kendra/models/qwen3-0.6b/Qwen3-0.6B-Q8_0.gguf` |
| `manifests/models.yaml` | `text_brain`, `provisioned-and-verified-pi-imac-parity` |
| `scripts/fetch_local_models.py` | `--llm` → 0.6B; `--deep-llm` → 4B, opt-in |

Qwen3-4B is retained only as an offline development model. It is explicitly
**not** the companion runtime: it breaks Pi latency parity. Do not install it
on the Pi.

Measured on this iMac: 639 MB Q8_0 loads in **1.4 s** across 6 threads at
`-c 4096`. Model stays resident; it is never reloaded between turns.

### Quantization

Ship **Q8_0 on both machines**, not Q8 on the Mac and Q4 on the Pi.

At 0.6B the memory argument for Q4_K_M is weak — roughly 380 MB versus 639 MB
on an 8 GB Pi. The real Pi bottleneck is memory *bandwidth*, so Q4_K_M is worth
benchmarking, but small models degrade proportionally more under aggressive
quantization than large ones do. Identical artifacts on both machines also
means a bug reproduced on the iMac is the same bug on the Pi. Only diverge if
`llama-bench` shows a decisive Pi win that blind A/B listening confirms.

## Routing: the fast/slow split already exists on a different axis

`kendra/agent/planner.py` already avoids paying planner cost on every
utterance. `_schemas_for_text()` keyword-matches the turn against the tool
registry:

- **No tool keywords** → `_plain_turn` / streamed plain path. Small prompt,
  charter plus ~1400 characters of retrieved memory, capped at 420 tokens.
- **Tool keywords present** → the schema-constrained planner loop, with
  least-privilege tool exposure.

So "Kendra, what time is it?" never pays planner latency. That is the
architecture already working.

What does **not** exist yet is a *reasoning* tier. `--reasoning off` is a
server-level flag in both launch scripts, so thinking is globally disabled and
nothing emits per-turn `/think` or `enable_thinking`. Adding selective thinking
is a third path, not a rewrite.

The llama.cpp build in `third_party/` (build 10478) supports what this needs:

```
-rea, --reasoning [on|off|auto]
--reasoning-budget N      # token budget for thinking; 0 ends immediately
--reasoning-effort LEVEL
```

`--reasoning-budget` is the direct answer to the thinking-loop risk: a hard
token cap, enforced by the server rather than by hoping the model stops. Serve
with `--reasoning auto` and select per request, with a bounded budget.

**Gate:** selective thinking ships only with a budget cap and a regression run
showing no increase in turns that exceed the deterministic time budget.

## Context budget

The Second Brain is what makes a 0.6B model practical, and the budgets are
already conservative in `config/default.yaml`:

| Setting | Value |
|---|---|
| `brain.context_character_budget` | 7000 |
| `brain.live_context_character_budget` | 1400 |
| `brain.retrieval_limit` | 12 |
| `brain.live_retrieval_limit` | 3 |
| `llm.max_tokens` | 700 (plain turns capped at 420) |

Kendra never sends whole conversation history. She retrieves, compacts, and
sends. Keep it that way; raising these is the easiest way to destroy perceived
latency.

Context size may go to `-c 8192` on the iMac, but keep the Pi at `-c 4096`
until a thermal soak test says otherwise.

## The real weight problem is vision, not language

| Asset | Size | Note |
|---|---:|---|
| Qwen2.5-VL-3B Q4_K_M + mmproj | **2.6 GB** | largest artifact by far |
| Qwen3-4B Q4_K_M | 2.3 GB | desktop-only, opt-in |
| Whisper base.en | 145 MB | ASR |
| Vosk small en-us | 108 MB | wake phrase |
| **Qwen3-0.6B Q8_0** | **625 MB** | always-on brain |
| Piper amy-medium | 61 MB | TTS |
| YuNet + SFace | 37 MB | face detect/recognize |

The language model is solved. A 2.6 GB VLM on an 8 GB Pi that also runs nine
services, a reflex loop, and TTS is the open problem. Replacing it is the next
model decision, and it is worth more than any further LLM tuning.

Leading candidate: **Moondream** (~2B, built specifically for edge image
description, proven running on a Pi 5 in the BMO build analyzed in
`docs/QWEN_VOICE_OPTIMIZATION.md`). Evaluate it as the 8081 endpoint's model
with pinned artifacts before considering any other change.

## Qwen3.5-0.8B: not yet, and not yet verifiable here

A unified tiny multimodal brain replacing "text model + separate VLM" is the
right shape for a small robot, and would erase the 2.6 GB line above.

I cannot verify this model's existence, license, or behavior from this
machine, so nothing about it belongs in `manifests/models.yaml` yet. Treat
every claim about it — Apache-2.0, image+text input, thinking/non-thinking
modes, and the thinking-loop warning — as unverified until checked against the
actual model card and a real GGUF conversion.

The staged position is right regardless: keep Qwen3-0.6B as the production
brain, and evaluate any unified multimodal candidate on the **8081 vision
endpoint** first, where a loop costs a slow scene description rather than a
frozen planner. Promote it only after it passes the regression suite. Reflex
safety is independent of the LLM, so a stalled planner never endangers the
robot — but it does ruin the companion.

## Acceptance targets

Targets, not promises, until `llama-bench` runs on both machines
(`llama-bench` is already built by the bootstrap).

| Metric | iMac | Pi 5 |
|---|---|---|
| Warm first-token latency | < 500 ms | < 1 s |
| Generation rate | faster than speech | ≥ speech rate |
| Model resident | always | always |
| Reload between turns | never | never |
| Thinking on ordinary turns | no | no |
| TTS waits for full response | no | no |

### Measured so far (iMac, 2026-08-17)

Through the desktop bridge, with the model warm:

- `snapshot`: ~150 ms
- `chat` (planner path): **7.8 s and 14.7 s** on two runs
- `voice_audio` round trip (ffmpeg → whisper.cpp → planner → Piper): **16.4 s**

These are far outside target. Note the measured path is the non-streaming
`chat`/`voice_audio` bridge command, not the streamed voice path that starts
Piper on the first phrase — perceived latency on the wake-phrase path should be
much better. Measure that path before optimizing anything.

## ASR: whisper.cpp, and why Moonshine was reverted

Moonshine `small-streaming-en` was adopted for lower latency, then reverted on
2026-08-17 after it was found to be unrunnable on both target machines.

`moonshine-voice==0.0.59` publishes exactly one wheel,
`py3-none-macosx_15_0_universal2`, and the `libmoonshine.dylib` inside it is
**arm64 only**:

```
incompatible architecture (have 'arm64', need 'x86_64h' or 'x86_64')
```

The wheel's `universal2` tag is wrong, so pip installs it happily on an Intel
Mac and it fails at the first spoken word. There is no `linux_aarch64`,
`manylinux2014_aarch64`, or `manylinux_2_28_aarch64` wheel at all, so the
Raspberry Pi 5 could never have run it either.

`voice.asr.provider` now genuinely selects an engine:

- `whisper_cpp` (default) — builds from source on x86_64 macOS and aarch64
  Linux; same engine and same `ggml-base.en.bin` on both machines.
- `moonshine` — kept, and reports its own unavailability up front via
  `available()` instead of failing mid-turn.

`kendra doctor` now checks the selected engine's *runtime* availability, not
just that a model directory exists. A present model directory was not proof of
a working engine, which is exactly how this shipped broken.

Revisit Moonshine only when upstream publishes an x86_64 macOS build and a
Linux aarch64 build. Until then, whisper.cpp is the portable answer.

## Standing rule: pin models, never `-hf`

Provision with `scripts/fetch_local_models.py`, which downloads pinned public
artifacts and verifies SHA-256 against `manifests/software-lock.txt`. Do not
start a server with `-hf owner/repo:QUANT`; it fetches at launch, floats
against upstream, and produces a machine whose actual weights nobody recorded.

A `llama-server` process started with `-hf ggml-org/Qwen2.5-VL-3B-Instruct-GGUF`
was found running on port 8081 during this audit — which is why the repo's own
VLM script and the running process disagreed. Use the scripts.


## Research round 2026-08-18: next-generation ears and brain candidates

All candidates verified against actual Hugging Face artifacts and our pinned
llama.cpp build — not blog claims.

### ADOPTED: Parakeet TDT 0.6B v3 (int8 ONNX) — her ears, effective now

Head-to-head on the same utterance, CPU only (the Pi's execution path):

| Engine | Warm latency | Transcription |
|---|---:|---|
| whisper small.en (server) | 3.40 s | perfect |
| **Parakeet TDT int8** | **0.28–0.31 s** | perfect |

11x faster with a better published WER (it leads the open ASR leaderboards
that whisper small trails). Runs in-process via `onnx-asr` (pure Python) on
onnxruntime — the runtime already shipped for memory embeddings — with wheels
for x86_64 macOS and Linux aarch64: full Pi parity, one less server. CoreML
EP fails on its dynamic shapes; `CPUExecutionProvider` is forced everywhere,
which is also what the Pi runs. Fallback chain preserved: parakeet →
whisper-server → whisper-cli. Files pinned at
`models/parakeet/parakeet-tdt-0.6b-v3-onnx`
(source istupakov/parakeet-tdt-0.6b-v3-onnx@8f23f0c0). The moonshine-voice
wheel is retired from the voice extra (broken on both targets); Moonshine's
raw ONNX artifacts remain a viable emergency fallback but Parakeet beats it
on accuracy at comparable speed.

### QUEUED: Gemma 4 E2B IT — candidate to replace brain AND eyes in one model

Verified: `ggml-org/gemma-4-e2b-it-GGUF` ships Q4_0/Q8_0 **plus an mmproj
(it is multimodal — vision built in) plus an MTP sidecar (multi-token
prediction — llama.cpp-native speculative speedup)**. Our pinned llama.cpp
b10478 already contains the `gemma4` architecture. If its conversational
quality matches community reputation, one model could replace Qwen3-1.7B +
Moondream (≈3.0 GB combined) — unifying her mind and eyes, with MTP as the
latency bonus. A/B protocol: bench pp/tg with and without MTP, persona probe,
Neptune-recall, sight description quality vs Moondream, memory footprint —
run on a CALM machine per the post-mortem rule.

### QUEUED: Qwen3.5-2B — brain candidate, higher friction

Verified present (also multimodal: video preprocessor in repo) and our
llama.cpp knows the Qwen3.5 graph — but no official GGUF yet; requires local
conversion or waiting for ggml-org/unsloth artifacts. Second in line behind
Gemma 4 E2B: same-family continuity (chat template, thinking modes, sampling)
is worth real migration savings if quality is comparable.

### Rejected this round

- vLLM / SGLang: GPU batch-serving engines; no practical aarch64/CPU story.
  llama.cpp is the correct engine for this fleet.
- AWQ: GPU-runtime quantization; GGUF is the edge equivalent (already used).

### Also staged (from the prior round, calm-machine pending)

- unsloth calibrated Q4_K_M of Qwen3-1.7B (sha b139949c…, on disk) vs our
  Q8→Q4 requant: quality A/B.
- Speculative decoding: retired Qwen3-0.6B as draft (`-md`) for the 1.7B —
  superseded if Gemma 4 E2B + MTP wins, since MTP is the same idea built in.


### Gemma 4 E2B on the Pi — official numbers (research addendum, 2026-08-18)

Google's LiteRT-LM runtime with XNNPACK reaches **~99 tok/s prefill /
~9 tok/s decode at 1,432 MB peak** for Gemma 4 E2B IT on a Pi 5 — the
1.4 GB figure (per-layer-embedding offload) is the headline: brain + vision
in less memory than Qwen3-1.7B alone. Two paths to evaluate, in order:
1. **llama.cpp GGUF** (ggml-org artifacts; arch already in our build) — keeps
   one runtime across the whole fleet; measure whether PLE offload exists
   there or memory balloons.
2. **LiteRT-LM (`litert-cli`)** — a second runtime, but Google's official
   Pi-blessed path with the 1.4 GB profile; acceptable as the brain's engine
   if llama.cpp can't match the footprint (services talk OpenAI-compatible
   HTTP either way).

Pi setup notes adopted from the same research: NVMe boot (already planned),
Raspberry Pi OS Lite 64-bit (already planned), active cooling REQUIRED
(A76 throttles under sustained generation), threads 2–4 (memory-bus bound,
matches our -t 3/4 practice). Their 4 GB dphys-swapfile advice applies only
to NVMe systems; on SD our zram-only policy stands.


## VERDICT 2026-08-18: Gemma 4 E2B IT is her mind — brain and eyes in one model

Evaluated live on the iMac (llama.cpp b10478, arch supported, artifacts
pinned: Q4_0 8e30dff3…, mmproj Q8_0 9406f99c…, mtp 718d3a44…).

| Probe | Qwen3-1.7B + Moondream | Gemma 4 E2B (one model) |
|---|---|---|
| Recall in context | good | **perfect, 2.1 s** |
| Agency/disagreement | template-ish | **night-and-day better prose** |
| Persona | needs guards | **natively in character** |
| Sight quality | accurate | **accurate (21 s turn incl. describe)** |
| Generation | 16.7 tok/s | 12.2 tok/s (~27% slower) |
| Prefill | 149 tok/s | 88 tok/s |
| Resident memory | ~3.0 GB (two servers) | **~2.6 GB (one server)** |

Trade accepted: ~27% slower tokens for dramatically smarter tokens, minus one
server, minus 0.4 GB, single unified mind = simpler Pi transplant.

Key integration facts (hard-won):
- Gemma 4 thinks by default; `--reasoning-budget 0` does NOT stop it. The
  template flag is the same one Qwen uses — `chat_template_kwargs.
  enable_thinking` — so kendra/llm.py worked unchanged.
- **MTP is not a CPU win**: 11.4 tok/s with the sidecar vs 12.2 without in
  llama.cpp speculative mode. It is a GPU/LiteRT feature; skip on this fleet.
- Vision and language share port 8080 (`--mmproj` on the brain server);
  `vision.semantic_vlm_url` points at 8080. Moondream (8081) and Qwen3-1.7B
  remain on disk as one-line rollbacks (`KENDRA_LLM_MODEL`, KENDRA_START_VLM=1).
- Pi expectations: LiteRT-LM claims 99/9 tok/s at 1,432 MB peak; the
  llama.cpp path should be measured on the Pi first for runtime uniformity.


## FINAL HYBRID 2026-08-18: right organ, right model

Gemma-for-everything overreached: 88 tok/s prefill made research and sight
turns crawl. Final assignment — Gemma 4 E2B language-only on 8080 (best
conversation per token anywhere near this size), Moondream vision on 8081
(4s warm describe), Parakeet ears (0.3s), Piper voice, MiniLM memory.
Research-only questions skip the planner: search → top-3 snippets ×350 chars
→ one streamed reply on the prewarmed prefix. Measured research turn: 8s
(was 252s at worst). Rollbacks unchanged (KENDRA_LLM_MODEL / KENDRA_START_VLM).


## Penguin-VL analysis (research/vision.pdf, Tencent, 2026-03) — verdict 2026-08-18

**As a model: not Pi-deployable today, watch-list.** Custom architecture
(Qwen3-1.7B decoder + 400M Qwen3-0.6B-initialized vision encoder), safetensors
+ custom Python only — no GGUF, no llama.cpp arch support, no ONNX. IF a
ggml-org conversion appears it becomes the top eyes candidate: best-in-class
2B real-world perception (RealWorldQA 70.2 vs Qwen3-VL-2B 63.9), fine detail
(V-star 83.8), explicit grounding+counting training (the finger-count cure),
and native-resolution input. Watch: huggingface tencent/Penguin-VL-2B.

**As a source of techniques: two adopted immediately, architecture-neutral.**
1. ADOPTED — Ambient motion-gated vision (their TRA keyframe principle,
   inverted): the eye-stream frames are diffed (96x54 gray, mean-abs);
   meaningful change + idle system + cooldown → one structured look → an
   unprompted `observation` memory. Verified live: she noticed Jonathan
   ("man in a red shirt... guitar and keyboard on either side") without
   being asked. Config: `vision.ambient`. Pi-identical code (diff runs on
   robot-camera frames there).
2. ADOPTED — Structured facet looks (their 9-facet captioning schema,
   condensed): ambient observations ask for subjects/actions/spatial
   layout/visible text/mood, so worldview memories carry structure, not
   just vibes.
3. CONTEXT — Their benchmark table shows Gemma3n-E2B far behind specialized
   2B VLMs on perception, which supports Kendra's hybrid (Gemma for language,
   dedicated model for eyes) over a unified-model design.

Everything else in the paper (training recipe, distillation losses, data
curation) is for model builders, not deployers — noted and set aside.


## SLM listicle assessment (2026-08-18): are the right models in place?

Verdict per organ against the "Top 10 SLMs 2026" list:

| Their pick | Our organ | Verdict |
|---|---|---|
| #1 Gemma-3n-E2B (general) | Gemma 4 E2B | AHEAD — we run its successor |
| #2 Phi-4-mini (reasoning) | thinking-mode on Gemma | REJECT — 2.5GB second brain doesn't fit Pi; bounded thinking covers it |
| #3 Qwen3-4B (agentic) | planner bypasses | REJECT — measured 22-37s/turn on our CPU; keyword routing beats model-side tool-calling here |
| #4 SmolLM3-3B (multilingual) | — | REJECT — English-first companion |
| #5 Qwen3.5-2B-MTP (vision+code) | Moondream | QUEUED EXPERIMENT — unsloth GGUF + mmproj exist (Apache 2.0), arch in our llama.cpp; object-detection training could cure fine counting. Next eyes A/B vs Moondream's 4s bar. |
| #6 Llama-3.2-1B (edge) | — | REJECT — below our quality floor |
| #7 R1-Distill-1.5B (CoT) | — | REJECT — over-generates reasoning tokens; latency poison for voice |
| #8 jina-embeddings-v5-small | MiniLM → **Qwen3-Embedding-0.6B ADOPTED** | jina REJECTED on license (CC-BY-NC). Qwen3-Embedding int8 ONNX: same 22ms encode, better cross-phrasing recall on her real corpus, 1024-dim, instruction-aware queries, Apache 2.0. Corpus re-embedded (245 memories). Pi note: use model_q4.onnx (~300MB smaller). |
| #9 jina-reranker-v3 | — | DEFER — worth adding only when the memory corpus is 10k+; at hundreds of memories, hybrid search suffices and the extra hop costs latency |
| #10 dots.ocr | Moondream reads scene text | REJECT — no document-ingestion pipeline on a companion robot |

Net: one adoption (embeddings), one queued experiment (Qwen3.5-2B eyes),
seven justified rejections, and confirmation the brain choice leads the field.
