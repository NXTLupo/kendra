# Personality & latency optimization plan (2026-08-18 audit)

Method: audited all 117 of today's Jonathan↔Kendra turns, classified failure
patterns by frequency, then sampled candidate interventions per pattern and
scored each against the observed evidence (which failures it would actually
have prevented) before implementing. Winners are marked SHIPPED.

## Audit results (117 turns)

| Pattern | Count | Example |
|---|---:|---|
| Confirms instead of acting | 10 | "I can check it when I need to. Want me to?" |
| Question-deflection endings | 7 | "...What do you think?" |
| Hedging/filler openers | 4 | "I see." / "That's fascinating!" |
| Robot status theater | 1 | "I am operating within optimal parameters." |
| Servile register | 0 | (killed in earlier rounds) |

## Problem A: confirms instead of acting (10x — the headline)

Candidates sampled:
- A1 Charter action clause ("do it, lead with the result") — global, cheap,
  imperfect adherence at 2B scale. **SHIPPED**
- A2 Targeted note strengthening: when evidence/scene/clock is ALREADY in
  context, the note forbids capability-talk explicitly. Attacks 8 of the 10
  observed cases (they all had the answer in context). **SHIPPED**
- A3 Deterministic act-guard: if a reply says "I can look/check" while
  evidence sits in context, regenerate once with a hard directive — code,
  not hope; same pattern that killed self-echo. **SHIPPED**
- A4 Auto-act on own offers (run research when she offers it): rejected —
  doubles worst-case latency and half the offers are for things she genuinely
  cannot do (calendar, personal plans).

## Problem B: question-deflection endings (7x)

- B1 Charter: end with substance; ask only genuine questions. **SHIPPED**
- B2 Register exemplar with a declarative ending. **SHIPPED**
- B3 Post-process stripping trailing questions: rejected — would amputate
  genuine curiosity, which is her identity, not a bug.

## Problem C: filler openers (4x)

- C1 Charter: first words carry content. **SHIPPED**
- C2 Deterministic strip of exact leading fillers ("I see. ", "I understand. ")
  before speech. **SHIPPED**

## Problem D: robot status theater (1x, Gemma trait)

- D1 Charter: answer "how are you" from real state (recent memories, current
  activity), never system diagnostics. **SHIPPED**

## Latency posture (current measured: chat 3-8s, research 8s, sight 21-28s)

- L1 conversation_max_tokens 200 -> 160: Gemma runs verbose; shorter tails
  end turns sooner at 12 tok/s. **SHIPPED**
- L2 Official Gemma sampling (temp 1.0 / top_k 64) vs current conservative
  values: QUEUED as an A/B — richer personality possible, needs a
  side-by-side before touching a working configuration.
- L3 Further prefill trims: rejected for now — evidence says remaining
  latency is generation + Moondream encode, not prompt size.

## Self-improvement posture

- Charter clause: when she catches herself failing (repetition, hedging,
  a wrong answer), she names it and adjusts — self-correction is identity,
  and her kendra_opinion memories let corrections persist. **SHIPPED**

## Success criteria (next audit should show)

- "I can look it up / want me to?" with evidence in context: 0 occurrences.
- Trailing reflex questions: under 2 per 100 turns, all genuine.
- Filler openers: 0 spoken (stripped or never generated).
- Research turns: under 15 s; chat first phrase under 5 s warm.


## First post-ship probe results (same day)

| Probe | Result | Verdict |
|---|---|---|
| "Go online and find out..." | Led with the finding + source, 40 s, zero capability-talk | PASS |
| "What time is it?" | Direct answer, 5 s, no offer to check | PASS |
| "How are you?" | Still opened with "operating at optimal capacity" | PARTIAL — Gemma's diagnostic tic outlasts the charter clause |

Next lever for the how-are-you tic: the queued L2 sampling A/B (official Gemma
temp 1.0 / top_k 64 tends to produce a more natural register than the
conservative Qwen-era values currently set), plus a targeted register
exemplar if the A/B alone doesn't move it.
