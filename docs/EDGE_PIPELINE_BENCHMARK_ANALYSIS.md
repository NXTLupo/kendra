# STMicroelectronics edge conversational-AI benchmark (slm.pdf) — what it means for Kendra

Source: Mazzone & Pau, *Performance Analysis of a Modular Framework for
Edge-Based Generative Conversational AI*, Appl. Sci. 2026, 16, 8157 (66 pp,
1000-iteration benchmarks, Intel Core Ultra 7 edge tier + RTX 5060 Ti tier).
Analyzed 2026-08-18 against Kendra's stack. Their testbed repo:
github.com/mazzonelorenzo1/audio-models-testbench.

## Verdicts on Kendra's current organs (their data, our stack)

| Organ | Kendra today | Paper's finding | Verdict |
|---|---|---|---|
| TTS | Piper (20M VITS) | Most efficient TTS in the whole study: RTF 0.034, 1.24 J/s. MOS only 2.847 vs Kokoro's 4.024 | KEEP for Pi; Kokoro is a quality option (below) |
| ASR | Parakeet TDT 0.6B int8, ~0.3 s/utterance | Their NAR finding (FunASR) mirrors Parakeet: parallel decoders beat autoregressive Whisper on CPU and get SLOWER on accelerators (transfer overhead > compute) | KEEP; validates CPUExecutionProvider choice |
| LLM sampling | Q4_0 GGUF | INT4 is the optimal edge precision: 75 ms TTFT vs 216 ms FP16, near-equal quality; INT8 pathological | KEEP |
| Streaming TTS | phrase-streamed (min 28 chars) | Punctuation-chunked (~5 words) is Pareto-optimal: word-by-word wastes 0.8 s overhead, full-sentence 376 ms TTFA | KEEP — our accumulator ≈ their optimum |
| Prompt discipline | stable-prefix caching | Context 15→2000 tokens raises TTFT at every precision → "aggressive context pruning is mandatory" | KEEP — validates the volatile-tail design |
| Thermals | threads capped, active cooler in BOM | CPU-bound STT hit 98 °C and throttled; "naive CPU-only architectures are invalid for continuous edge agents" | KEEP + reinforces the yield-gate work |
| Concurrency | yield gates (ambient defers, consolidation waits) | "Enforcing strict concurrency on the edge SoC leads to severe hardware starvation" — they mandate HALF-DUPLEX sequential pipelines on edge | KEEP — the paper independently arrives at tonight's fix |

Their optimized perceived-latency model (Latency = STT·RTF_in + 10-token
first chunk + 3 s·RTF_TTS) lands at **778–958 ms** for standard/conversational
queries on the constrained tier. Kendra's equivalents: ASR 0.3 s +
first-phrase TTFT ~1-2 s + Piper ~0.1 s. Our floor is the LLM TTFT (prefill),
which is exactly where slot persistence and prewarm already point.

## New levers we have NOT employed (ranked)

1. **LFM2 (Liquid) as brain candidate — the one genuinely new architecture.**
   Their LFM2-24B-A2B MoE (2B active) hit 39.2 tok/s on GPU with 0.926
   semantic score and 90% tool-format success *when parsed as Pythonic calls
   instead of JSON*. 24B doesn't fit our RAM, but the same family ships
   **LFM2-8B-A1B (~4.5 GB Q4, 1B active)** and dense LFM2-1.2B/2.6B — hybrid
   short-convolution + GQA backbone designed for CPU cache behavior, llama.cpp
   supported. A/B against Gemma 4 E2B: prefill tok/s (our dominant cost),
   decode tok/s, and her conversational register. CAVEAT from their data: LFMs
   degrade when forced into JSON tool schemas (37.5%→90% FSR switching to
   native syntax) — our planner emits JSON, so a brain swap would need the
   planner's tool syntax adapted. Try AFTER the Pi transplant baseline is
   stable, or as a measured experiment day.
2. **Kokoro TTS (82M) as her "beautiful voice" option.** MOS 4.024 (best in
   study, humanlike) vs Piper 2.847 (robotic-ish). CPU RTF 0.255 — real-time
   with 4x margin on the Intel tier, likely ~1.0 borderline on Pi. Her voice
   is her identity — swapping it changes who she sounds like, so this is
   Jonathan's call, not an engineering default. iGPU offload is a trap for
   TTS (RTF 0.255→1.330); CPU only.
3. **Moonshine as the Pi RAM-relief ASR.** Moonshine Base: 61M params, WER
   0.051, RTF 0.064, no zero-padding (variable-length RoPE attention), 1.4 J/s.
   Parakeet is more accurate but ~700 MB resident; Moonshine Base would free
   ~600 MB of the Pi's mind budget if the 16 GB gets tight. Fallback option,
   documented not adopted.
4. **Race-to-sleep as a battery law.** Finish fast and idle beats slow-and-
   steady: their Moonshine (0.80 J/s at 100% CPU burst) beats lighter-load
   slower models. For the robot: keep bursts short, never hold background
   load — which is what the quiet-gap consolidation and ambient yield already
   implement. On-battery, this is why mlock + prewarm (avoid re-paging work)
   also saves energy, not just time.
5. **Their future-work list matches our roadmap**: E2E audio-language models
   (LFM2.5-Audio measured RTF 9.12 on CPU — NOT viable on edge yet, so our
   cascaded pipeline stands); dynamic hardware routing (their "cognitive
   hypervisor" ≈ our deterministic keyword routing, already shipped); on-device
   LoRA personalization (their open question — matches our control-vector
   experiments queue).

## What we explicitly reject

- **Qwen3-TTS / VoxCPM / OuteTTS** (autoregressive/diffusion audio LMs):
  RTF 1.9–37.8 on CPU — "the autoregressive nightmare of ALMs." Never on Pi.
- **iGPU/NPU offloading advice**: doesn't transfer — Pi 5 has neither an NPU
  nor a usable iGPU for this; their CPU columns are our only relevant tier
  (and there, our choices already match their winners).
- **Whisper large variants on CPU**: RTF >1.2, 98 °C, 52 J/s. Already rejected
  in our earlier ASR round; the paper confirms with 1000-run rigor.
