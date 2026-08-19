# Comprehensive enhancement set from the Libra paper (LLM_Research.pdf)

Source: "An Efficient Context Management System for On-Device LLMaaS"
(SenSys'26) — chunk-wise KV-cache compression, swap/recompute pipelines, and
lifecycle management for persistent LLM contexts on 8GB-class edge devices.
Read in full (15 pages). Compiled against the five requested improvement
areas; each item marked SHIPPED / QUEUED / REJECTED with evidence.

## 1. Context / memory / second-brain management

- **SHIPPED — Libra-style KV persistence for her prompt prefixes.** Kendra's
  exact miniature of the paper's problem: three stable prefixes
  (conversation, planner, consolidation) thrash two server slots; every
  switch cost a full re-prefill (measured cache similarity 0.18, 95s worst
  prefill). Mechanism: `--slots --slot-save-path` + explicit save/restore
  orchestration in `prewarm_conversation` (restore → warm → save). Measured:
  **prewarm after planner eviction 18s → 0.3s**; chat after restore 3.0s.
  The automatic `--cache-idle-slots` flag was REJECTED with evidence: 16s per
  token save-churn pathology on build b10478.
- **SHIPPED (prior rounds, same philosophy)** — rolling history note,
  instant-queued consolidation, Qwen3-Embedding-0.6B semantic memory,
  observation/opinion memory kinds, probe-source quarantine.
- **QUEUED — persistent conversation KV across restarts**: slot files survive
  server restarts; restoring at service boot would make her first turn of
  the day warm. One prewarm call at startup already exists — extend it to
  restore before the first warm (works today via the same file).
- **REJECTED — chunk-wise 2/4/8-bit KV compression (the paper's §3.2)**:
  llama.cpp exposes only whole-cache quantization, which we measured as a
  3.5x prefill tax without flash attention. Libra's per-chunk
  attention-scored compression needs engine surgery; noted for upstream
  llama.cpp watching, not for us to build.

## 2. Reasoning and intelligence

- The paper is systems-only here, but its persistence enables: **SHIPPED** —
  bounded selective thinking (128-token budget) stays affordable because the
  thinking prefix no longer evicts the conversation KV permanently.
- **QUEUED** — restore-before-planner: the same save/restore treatment for
  the planner prefix (planner.bin) would cut tool-turn prefill identically.
  Deferred only to keep this change surgical; mechanism proven.

## 3. Latency reduction / caching (the paper's core)

- **SHIPPED** — the headline: context switching 18s → 0.3s (60x, matching
  the paper's claimed order-of-magnitude). Combined stack now: prewarm
  during capture + prefix cache-reuse + slot save/restore + snippet-capped
  research + planner bypasses for sight/research + 160-token replies.
- **SHIPPED (validated by the paper's Observation #2)** — "LLM contexts must
  be persistent" is exactly why her prewarm + slot files exist; the paper's
  22.9s recompute measurement mirrors our 18.6s cold prefill.
- **REJECTED** — swapping-recompute pipeline (§3.3): llama.cpp's restore is
  already I/O-only at 0.03s from NVMe/SSD; recompute overlap adds nothing at
  our context sizes. Revisit on the Pi only if SD-card I/O makes restores
  slow (put runtime/slots on NVMe or zram-backed tmpfs there).

## 4. Visual intelligence

- Paper does not address vision; its persistence principle transfers:
  **QUEUED** — Moondream's fixed perspective-prompt prefix could use the
  same slot save/restore if sight cold-starts ever dominate again (currently
  4s warm, not the bottleneck).
- SHIPPED (prior round, aligned philosophy): ambient motion-gated vision —
  spend compute only on changed frames, the visual analogue of "don't
  recompute what you can restore."

## 5. Voice chat and speech

- **SHIPPED** — the practical effect of §1-3: the wake→speak loop no longer
  pays prefix-switch penalties. Measured conversation turns hold ~3s even
  immediately after research/tool turns (was: first chat after a tool turn
  re-prefilled the entire charter).
- **SHIPPED (validated)** — the paper's AoT swap-out ("hide reclaim latency
  at return time") is mirrored by saving the conversation slot right after
  each prewarm rather than at need-time.

## Configuration record

- Launcher + kendra-llm.service: `--slots --slot-save-path <runtime>/slots/`
  (Pi: /var/lib/kendra/slots, ExecStartPre creates it).
- kendra/llm.py: `slot_states()`, `slot_action(id, save|restore, filename)`.
- planner.prewarm_conversation: restore-if-evicted → warm → save.
- Slot files: `conversation.bin` (≈50MB per saved prefix on Gemma 4 E2B).


## Post-implementation verdict (same day, hours later) — HONEST REVERSAL

The queued items were implemented and then **measured as broken at the
engine level**: llama-server b10478's slot-save API writes 0 tokens
(52-byte files), and restoring those empty states CLEARS healthy prefix
cache — tool turns degraded from ~12s to 159s. All orchestration reverted;
`slot_states`/`slot_action` helpers remain dormant with the failure
documented inline. The earlier "0.03s restore" measurement was a confounded
test: ordinary prefix-cache retention, not the restore, produced the fast
number. RE-ENABLE ONLY after a llama.cpp upgrade demonstrates non-empty
saves. Moondream prefix slot: triaged OUT — its cacheable prefix is <100
tokens (~1s), negligible.

What this round actually shipped instead (found while verifying):
- **Retrieval relevance gate** (store.search): memories with semantic
  similarity <0.18 and no lexical match are dropped, whatever their
  recency/salience. Root cause of "answers poisoned with Raspberry Pi
  topics": for off-topic questions, salience+recency outvoted near-zero
  semantic scores and the freshest memories (probe-driven RPi research
  facts) flooded every prompt. Verified: night-sky question, zero leak,
  5.4s.
- 11 probe-origin researched facts purged; probe-source turn exclusion was
  already in place.


## FINAL IMPLEMENTATION (same day, corrected) — the reversal reversed

The "engine broken" verdict was wrong; the bug was ours. Root cause found by
inspecting the actual GET /slots JSON: **this build exposes no `prompt`
field**, so needle-matching could never locate a slot, and the first manual
test saved an idle slot (n_saved: 0 → 52-byte file) while the request lived
in the other one. Corrected test: saving the token-bearing slot wrote
n_saved=929 / 12 MB, and restore returned n_restored=929 in 0.04 s. One more
engine fact: restored KV is invisible to the prefix router, so requests must
pin `id_slot`.

Final design — deterministic slot ownership (Libra by construction):
- Slot 0 = conversation (every chat/sight/research reply pins id_slot=0).
- Slot 1 = planner/tool JSON (pinned likewise).
- Boot: `_boot_restore_slots()` loads conversation.bin/planner.bin once.
- Ahead-of-time saves after prewarm (background); saves verified real
  (conversation.bin now 15 MB on disk).

Verified under rotation on the live stack:
- chat 4.3 s → tool turn → **chat 3.9 s (conversation slot untouched — the
  core win; previously every tool turn forced a full charter re-prefill)**
- repeated tool turns 140 s → 60 s (slot-1 retention).
Tool-turn absolute latency remains dominated by the volatile planner tail +
two LLM rounds — a separate, pre-existing cost, not a slot regression.

Lesson recorded: verify save payload sizes (n_saved > 0) before trusting a
persistence API, and never diagnose an engine while your own slot targeting
is unverified.
