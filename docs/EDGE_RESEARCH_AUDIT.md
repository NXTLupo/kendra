# Six-source research audit + charter audit (2026-08-18)

## Charter audit verdict

The original targets (warm/direct/adult companion, peer not assistant,
epistemic honesty, consent-gated sensing, fail-closed autonomy) all survive
in charter/charter.md, now strengthened by the day's additions: agency and
evolving opinions, self-recognition, act-then-speak, spoken-register rules,
inner-life answers, and self-correction as identity. One documented residual
risk: Gemma's diagnostic tic ("operating at optimal capacity") under the
how-are-you pattern — mitigation queued (official Gemma sampling A/B).

## Source verdicts

1. **Google LiteRT/Gemma-on-Pi blog** — banked earlier (99/9 tok/s @1.4GB
   E2B; litert-cli path documented as Pi alternative). New: heterogeneous
   execution (vision/audio to VideoCore GPU, LLM on CPU) recorded as a
   Pi-phase experiment; EmbeddingGemma 300M noted but our Qwen3-Embedding
   upgrade already outbenchmarks that class.
2. **Google Cloud Gemma docs** — REJECTED: purely cloud-platform (MaaS/vLLM
   on GCP); nothing for an offline robot.
3. **awesome-ai-agents-2026** — one adopted pattern: **scheduled memory
   review between sessions ("dreaming") — SHIPPED, see below.** Hosted voice
   stacks (Cartesia/ElevenLabs/Deepgram) rejected on the no-cloud invariant;
   their numbers noted as UX bars (40-90ms TTS is the ceiling to envy).
4. **awesome-llm-apps** — validates our architecture (local hybrid RAG,
   per-user memory, fully-local voice); nothing there we lack.
5. **SLM offload forum thread** — the Wi-Fi offload idea CONTRADICTS
   Kendra's offline-first invariant (robot must work with radios off);
   rejected as architecture, though DietPi's low-RAM footprint (~1.5GB idle
   savings) is noted for the Pi image choice.
6. **Rovai EdgeML ebook (Pi 5 measurements)** — independent validation on
   target hardware: **MoonDream 3-4x faster than Gemma-class vision on Pi**
   (our eyes choice), Gemma vision captioning >3 minutes on Pi (confirms
   the unified-Gemma-vision reversal), E2B-class fastest brain, thermal
   thresholds (fan curves 60/67.5/75C, throttle 80C) folded into INSTALL_PI
   guidance, Q4_K_M as the standard quant (ours).

## SHIPPED this round: dreaming (idle memory review)

`BrainConsolidator.dream()` + idle loop in the brain service: after 30+
quiet minutes, at most every 6 hours, one bounded LLM call (tool slot,
300 tokens) reviews her 20 newest memories, retires duplicates, distills at
most two first-person insights (kind=insight, provenance=inferred).
First forced dream on real memories: retired 3 duplicates and produced
"I see a recurring theme of a man in a chair interacting with technology
(laptop) and often holding or near a guitar" — her first self-derived model
of Jonathan's daily life. Config: brain.dreaming.
