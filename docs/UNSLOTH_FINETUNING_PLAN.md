# Unsloth fine-tuning plan for Kendra

Sources analyzed 2026-08-19: unsloth.ai/docs fine-tuning guide + LoRA
hyperparameters guide + repo README (full sweep), Rovai's EdgeML ebook (Pi
SLM + fine-tuning chapters), and research/finetuning.pdf (Tencent "Efficient
Multimodal LLMs" survey, arXiv 2405.10739). Method: reinforcement inference —
every candidate below carries a prediction and a measurable reward; we keep
what measures well after the first training round and record rejections.

## The hardware truth first

**The Intel iMac cannot train.** Unsloth Core is CUDA/Linux/Windows;
Unsloth's macOS training path is MLX, which is Apple-Silicon-only; the Metal
build on Intel Macs covers **GGUF inference only**. This does not block the
plan — it shapes it:

- **Train on free Colab T4** (Unsloth's canonical path; QLoRA on a 1-4B model
  needs 3.5-5GB of the T4's 15GB).
- **Everything else happens on the iMac**: dataset export from her own brain
  (`scripts/export_finetune_dataset.py`, already built — 215 curated real
  conversation pairs in ChatML `messages` format), evaluation probes, and
  deployment into the existing llama.cpp stack.
- **Deployment is zero new infrastructure**: `save_pretrained_gguf(...,
  "q4_k_m")` produces a file that drops into `models/` and the existing
  start scripts, identically on the iMac and the Pi (Rovai's Pi workflow is
  literally "fine-tune with Unsloth elsewhere, copy GGUF to the Pi").

## What is and is not tunable in her stack

| Organ | Model | Unsloth? |
|---|---|---|
| Brain | Gemma 3n-E2B | **YES** — dedicated notebook; text-only fits free T4; unsloth is the only framework with the fp16 Conv2D overflow fix; GGUF gotchas: `per_layer_token_embd` ≥ Q8_0, GGUF is text-only (fine — `KENDRA_BRAIN_VISION=0` already) |
| Brain rollbacks | Qwen3 0.6B/1.7B/4B | **YES** — full family; keep 75/25 reasoning mix if thinking mode must survive |
| Brain candidate | LFM2 / LFM2.5 | **YES (as LFM2.5)** — SFT + GRPO configs documented, incl. LFM2.5-VL |
| Eyes | Moondream2 | **NO** — custom architecture, no unsloth path |
| Ears | Parakeet TDT | **NO** — NeMo-based; (Whisper Large V3 IS tunable — our fallback ASR) |
| Voice | Kokoro | **NO** — StyleTTS2 lineage, not transformers-compatible; supported TTS models are 0.5-3B autoregressive, which the ST Micro benchmark measured at RTF 1.9-37.8 on CPU — rejected for Pi regardless |

## Candidates, ranked (prediction → reward signal)

### C1. Kendra-voice LoRA on Gemma 3n-E2B — DO FIRST
One QLoRA run (r=16, alpha=32, LR 2e-4, ≤2 epochs, all seven target modules)
on a unified dataset: the 215 curated real pairs + synthetic
charter-conformant dialogs to ~1,000 rows (unsloth: 100 minimum, 1,000+
optimal; synthesize from ≥10 seeds; ONE dataset, never sequential runs).
The dataset already trains AGAINST every failure this session fought by
regex: diagnostics register, capability denial, meta-narration, metric-speak.

Predictions:
- **Naturalness/cuteness**: the tic dies in the weights instead of being
  caught by `_CAPABILITY_TALK` regens — each caught regen today costs a full
  extra generation (~5-15s); guard-trigger rate should drop to ~zero.
- **Latency, the big one**: with behavior baked in, the runtime charter
  shrinks from ~2,000 tokens to a ~300-token identity note. At the iMac's
  measured 50-75 tok/s prefill that is **20-30s off every cold prefill** and
  a smaller stable prefix to cache, restore, and save. On the Pi (~19.5
  tok/s prompt eval per Rovai) the same shrink is worth **~85 seconds** on a
  cold start — prompt eval dominates short exchanges there, so this is the
  single largest Pi latency lever we own.
- **Intelligence**: modest; SFT mainly transfers register and honesty
  patterns. (The Tencent survey's VILA finding: text-only conversational
  tuning also improves multimodal answer quality — her sight answers should
  get more natural for free.)

Reward signal (measure before/after on the same probes): register probe
suite (mic-check, "how are you feeling", "who do you see", sight, research),
guard-trigger count per 50 turns, warm/cold turn timings with the shrunk
charter, and Jonathan's ear.

### C2. Tool-call reliability — merge into C1's dataset
Add planner examples (real planner JSON rounds from logs, cleaned) so the
2B brain emits valid tool JSON more reliably. Verifiable reward exists
(parse success rate), so if SFT is insufficient this is the ONE place GRPO
is justified later (unsloth: GRPO wants ≥1.5B params, ≥500 rows, ≥300
steps, 12+ hours — Phase 2, not now; "for most use-cases SFT is
sufficient").

### C3. Embodiment self-knowledge — style only, not facts
Include transplant/build Q&A pairs so she TALKS about her body plan
naturally — but keep the deterministic `_build_plan_note` as the source of
facts. Facts baked into weights go stale the day the build changes; the
wiki/manifest stays the truth, the LoRA only learns how to speak about it.

### C4. Same LoRA recipe on Qwen3-1.7B (rollback parity)
Cheap second Colab run so the fallback brain speaks Kendra too. Keep the
75% reasoning / 25% conversational mix unsloth prescribes for Qwen3.

### C5. LFM2.5 fine-tune — the creative unlock (Phase 2)
The LFM2-8B-A1B A/B measured Gemma-equal speed with 2x knowledge capacity,
blocked on LFMs' weak JSON tool syntax (37.5% vs 90% Pythonic). Unsloth
fine-tunes LFM2.5 — so a LoRA can TEACH it her planner's JSON format,
dissolving the adoption blocker. If the A/B day goes well, this is the
brain-upgrade path: LFM2.5 + Kendra-voice + tool-JSON LoRA, GGUF Q4_K_M,
~9.7GB Pi mind budget (fits 16GB).

### C6. Whisper fine-tune on Jonathan's voice — only if needed
Parakeet (untunable) is accurate today. If proper-noun errors persist
("Kendra", guitar jargon), fine-tune the fallback Whisper on ~1-3h of his
audio (unsloth TTS/STT notebook, free T4) and promote it. Not now.

### Explicitly rejected
- **Training on the iMac or the Pi** — no supported path; Rovai calls on-Pi
  fine-tuning impractical outright.
- **TTS fine-tuning** — Kokoro unsupported; the supported TTS models are
  autoregressive audio LMs the ST benchmark already disqualified on CPU.
- **Moondream fine-tuning** — no path; her eyes improve via the Qwen3-VL /
  LFM2.5-VL A/B route instead (both ARE unsloth-tunable if adopted).
- **GRPO for conversation** — no verifiable reward for "cute and warm";
  SFT is the tool. GRPO reserved for tool-JSON (C2/C5).
- **Baking facts into weights** — memories/wiki/manifest remain the truth
  store; weights carry register, not knowledge.

## Execution recipe (C1, one sitting)

1. iMac: `.venv/bin/python scripts/export_finetune_dataset.py` → upload
   `exports/finetune/kendra_voice_sft.jsonl` to Colab.
2. Colab free T4: unsloth Gemma 3n-E2B notebook; QLoRA r=16 alpha=32
   dropout=0 LR 2e-4, 2 epochs, effective batch 8-16, eval split 10%,
   target loss 0.5-1.0 (loss→0 = overfit, stop). Synthesize to ~1,000 rows
   in-notebook from the curated seeds first.
3. Export `save_pretrained_gguf("kendra-gemma3n-e2b-v1", quantization_method
   ="q4_k_m")` — verify the chat template/EOS matches llama.cpp inference
   (the #1 bad-GGUF cause per unsloth).
4. iMac: drop under `models/gemma3n-kendra-v1/`, point
   `KENDRA_LLM_MODEL` at it, shrink the charter to the identity core, run
   the reward-signal probes, A/B against stock. Keep or reject on the
   measurements. Rollback is one env var.
5. Pi: the same GGUF rides the transplant untouched.

## Pi-specific notes (from Rovai's measurements)

- Gemma 3n E2B outpaces Llama-3.2-3B on the Pi 5 — her brain choice holds.
- Prompt eval ~19.5 tok/s dominates Pi turn latency → the C1 charter shrink
  matters MORE on the Pi than on the iMac.
- Active cooler mandatory at sustained load (already in BOM); NVMe over SD
  (already in BOM); LiteRT-LM (TTFT 1-2s from NVMe, 2.7GB RAM for E2B INT4)
  stays on the runtime watch list — a fine-tuned model would need `.litertlm`
  conversion, so llama.cpp remains primary.
