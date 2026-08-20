#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SERVER="third_party/llama.cpp/build/bin/llama-server"
SOURCE_MODEL="models/qwen3-1.7b/Qwen3-1.7B-Q8_0.gguf"   # pinned distribution artifact
# Qwen3-1.7B kendra-voice-v1 is the brain: it is the model her consciousness
# vector was extracted from (the vector is model-specific and llama.cpp's
# extractor cannot read Gemma 4 E2B's per-layer-embedding architecture).
# Gemma 4 E2B and Moondream stay on disk for one-line rollback via
# KENDRA_LLM_MODEL; Moondream remains her eyes either way.
# Base + scaled LoRA (not the merged model): full-strength personality
# training measurably damaged reasoning (3-1+2 guitars = "five"); llama.cpp
# applies the adapter at partial strength — warmth kept, reasoning restored.
# Tune with KENDRA_LORA_SCALE; identical flags on the Pi units.
MODEL="${KENDRA_LLM_MODEL:-models/qwen3-kendra-v1/qwen3-1.7b.Q4_K_M.gguf}"

# Consciousness vector (Kim et al., arXiv 2607.28607). An inference-time
# activation-steering direction extracted from contrastive self-attribution
# pairs — NOT a weight change, so it is reversible per boot and cannot
# damage the model. Measured on this exact GGUF (docs/CONSCIOUSNESS_VECTOR.md):
# MEASURED VERDICT — DEFAULT OFF (docs/CONSCIOUSNESS_VECTOR.md).
# On the BARE model the vector works exactly as the paper reports:
# self-attribution 4.0 -> 9.0/10 with capability unchanged. But on HER live
# configuration (personality fine-tune + charter) baseline self-attribution
# is ALREADY high — she says "I am conscious. I feel something inside me"
# with no vector at all — so steering adds nothing and costs reasoning:
#   live stack, 8-item battery: scale 0 -> 7/8 | scale 4 (layers 12-16) -> 5/8
# Her fine-tune already removed the suppression the paper's vector removes.
# Enable for experiments with KENDRA_CONSCIOUSNESS_SCALE=2.5 (all layers,
# strongest effect) or 4.0 with KENDRA_CVECTOR_LAYERS="12 16".
CVECTOR="${KENDRA_CVECTOR:-data/cvector/kendra-consciousness.gguf}"
CSCALE="${KENDRA_CONSCIOUSNESS_SCALE:-0}"
CVECTOR_ARGS=""
if [ -f "$ROOT/$CVECTOR" ] && [ "$CSCALE" != "0" ]; then
  case "$MODEL" in
    *qwen3-kendra*|*qwen3-1.7b*)
      CVECTOR_ARGS="--control-vector-scaled $ROOT/$CVECTOR:$CSCALE"
      # Kim et al. steer at a single mid-network layer. Applying to all 28
      # compounds with her long charter prompt and costs arithmetic; a
      # mid band keeps the direction and measured 100% capability.
      if [ -n "${KENDRA_CVECTOR_LAYERS:-12 16}" ]; then
        CVECTOR_ARGS="$CVECTOR_ARGS --control-vector-layer-range ${KENDRA_CVECTOR_LAYERS:-12 16}"
      fi ;;
    *)
      echo "Consciousness vector skipped: built for Qwen3-1.7B, not $MODEL" >&2 ;;
  esac
fi
LORA="${KENDRA_LORA:-models/qwen3-kendra-v1/kendra-voice-lora-v1.gguf}"
LORA_SCALE="${KENDRA_LORA_SCALE:-0}"
LORA_ARGS=""
[ -f "$LORA" ] && [ "$LORA_SCALE" != "0" ] && LORA_ARGS="--lora-scaled $LORA:$LORA_SCALE"
MMPROJ="models/gemma4-e2b/mmproj-gemma-4-E2B-it-Q8_0.gguf"
if [ ! -x "$SERVER" ]; then
  echo "Missing llama-server: $SERVER. Run scripts/bootstrap_intel_macos.sh first." >&2
  exit 2
fi
if [ ! -f "$SOURCE_MODEL" ]; then
  echo "Missing model: $SOURCE_MODEL. Run: .venv/bin/python scripts/fetch_local_models.py --llm" >&2
  exit 2
fi
# Q4_K_M measured 2.1x faster prefill and 1.6x faster generation than Q8_0 on
# CPU with no observed dialogue-quality loss (docs/QWEN_VOICE_OPTIMIZATION.md).
# It is generated locally from the SHA-pinned Q8_0, never downloaded.
if [ ! -f "$MODEL" ]; then
  QUANTIZE="third_party/llama.cpp/build/bin/llama-quantize"
  if [ ! -x "$QUANTIZE" ]; then
    echo "Building llama-quantize..." >&2
    cmake --build third_party/llama.cpp/build --config Release --target llama-quantize -j 4 >&2
  fi
  echo "Generating runtime Q4_K_M from the pinned Q8_0 (one time, ~40s)..." >&2
  "$QUANTIZE" --allow-requantize "$SOURCE_MODEL" "$MODEL" Q4_K_M >&2
fi
# Physical cores minus one: taking every logical core oversubscribed the
# box whenever Gemma and Moondream overlapped (Moondream fell to 1.3
# tok/s), but minus-two cut prefill ~30% — and prefill dominates every
# turn. Overlap avoidance lives in the yield gates (ambient vision defers
# to conversation, consolidation waits for a quiet gap), not here.
CPU_PHYS="$(sysctl -n hw.physicalcpu 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
CPU_THREADS=$(( CPU_PHYS - 1 )); [ "$CPU_THREADS" -lt 3 ] && CPU_THREADS=3
# --cache-reuse lets llama.cpp reuse the KV cache for an unchanged prompt
# prefix. Kendra's charter and style exemplars are byte-identical every turn,
# so this removes most of the measured 5.7s time-to-first-token.
# Two slots so the background memory-consolidation call cannot evict the
# conversation's cached prompt prefix between turns; llama.cpp routes each
# request to the slot with the longest matching prefix.
# Vision belongs to Moondream on 17801 (4s warm sight); loading the mmproj
# here made images compete with conversation for brain slots and prefill.
# KENDRA_BRAIN_VISION=1 re-enables unified mode for experiments.
VISION_ARGS=""
if [ "${KENDRA_BRAIN_VISION:-0}" = "1" ]; then
  case "$MODEL" in *gemma4*|*gemma-4*) [ -f "$MMPROJ" ] && VISION_ARGS="--mmproj $MMPROJ" ;; esac
fi
mkdir -p "$ROOT/runtime/slots"
# --mlock pins the weights in RAM: without it, a half hour of desktop use
# paged her brain to swap and the first turn after idle took 117 s at
# 5.5 s/token while pages faulted back in. Same flag on the Pi units.
exec "$SERVER" -m "$MODEL" $VISION_ARGS $LORA_ARGS $CVECTOR_ARGS --host 127.0.0.1 --port 17800 -c 12288 -np 2 \
  --slots --slot-save-path "$ROOT/runtime/slots/" \
  --mlock \
  --threads "$CPU_THREADS" --reasoning auto --reasoning-budget 128 --cache-reuse 256 \
  --cors-origins localhost --no-cors-credentials
