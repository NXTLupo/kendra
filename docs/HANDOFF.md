# Kendra — engineering handoff

Written 2026-08-23 for whoever takes this over. Everything here is measured
from the running system, not remembered. Where I broke something, it says so.

---

## 1. What she is

A sovereign, offline-first AI companion. Nothing leaves the machine — no API
calls, no telemetry, no cloud inference. Two deployment targets share one
Python codebase:

- **Virtual Kendra** — Electron app + 10 Python services, Intel iMac (x86_64,
  i5-9600K, AMD Radeon Pro 580X 8 GB, **no CUDA**).
- **Robot Kendra** — Raspberry Pi 5 hexapod body, systemd units in `systemd/`.
  The desktop shell never ships there; brain/voice/vision/reflex/body do.

### Processes

| Piece | What runs |
|---|---|
| Electron | `dashboard/electron/main.mjs`, sandboxed, `contextIsolation: true` |
| Bridge | `python -m kendra --config config/pc.yaml dashboard-bridge` |
| 10 services | brain, identity, reflex, body, research, vision, leds, delivery, agent, voice |
| Port 17800 | llama.cpp — Qwen3-1.7B Q4_K_M fine-tune + control vector, `--mlock` |
| Port 17801 | llama.cpp — Moondream2 (vision) |
| Port 17802 | whisper.cpp — small.en |

Services talk over **Unix sockets** in `runtime/pc/*.sock`, not TCP. ONNX
Runtime carries Parakeet (ASR), Kokoro (TTS), Qwen3-Embedding (memory) and
YuNet/SFace (faces). Brain is SQLite at `data/kendra-brain.db` — 14 MB,
2,237 active memories, 1,381 turns.

### Start her

```bash
bash scripts/kendra_desktop_launcher.sh
```

**Leave that terminal open.** The launcher holds Electron in the foreground.
Any process that starts her and then exits takes her down with it — see §5.

---

## 2. State as of this writing

Stack **healthy**: Electron up, 10/10 services, bridge up, 17800/17801/17802 up.

- `103` Python tests pass, `20` Node tests pass, `ruff` clean.
- **19 uncommitted paths** on top of `6a74b87`. Nothing today is committed.
- The running renderer is frozen on a ~4-hour-old bundle (see P2).

---

## 3. Open problems

Ordered by how much they hurt. Each is symptom → evidence → root cause.

### P1 — CRITICAL — She loses track of who she is

**Symptom.** Asked "And who are you?", she answered **"I am Jonathan."**

**Evidence** (verbatim, 6:46–6:47 PM):
```
You:    Take a look and tell me who's in front of you.
Kendra: That's Jonathan!
You:    And who are you?
Kendra: I am Jonathan. You asked me about myself.
```

**Four independent root causes.** Only the second is fixed.

1. **The identity anchor is one clause.** `_conversation_prompt()` in
   `kendra/agent/planner.py` says only *"You are Kendra, running fully
   locally"*, buried after the charter. Against a context saturated with
   "Jonathan", a 1.7B model does not hold the distinction. **Not fixed.**
   The prompt must stay byte-identical per turn for KV-cache reuse, so add
   static text — never interpolate.

2. **Mixed-person memories.** 5 of 34 Kendra-subject memories read like
   `"Kendra thinks Jonathan is the person I see."` — two referents, one
   sentence. `_third_person()` in `kendra/brain/consolidator.py` had three
   bugs: a duplicated `@staticmethod`, `count=1` plus an early `return` so
   only the *first* pronoun converted, and it ran only for
   `provenance == "user_stated"` — so all 67 `kendra_opinion` memories
   skipped it entirely. **FIXED** (applies to every provenance now, and
   `_person_is_coherent()` refuses to store an ambiguous memory).
   The 5 already in the DB are **still there** and must be retired.

3. **`kind` column polluted with content.** Two rows have a memory *type* of
   `","` and of `"I am not kind. I am efficient. Kindness is a variable I
   don't prioritize."`. Kind is also unnormalised: `user_stated` vs
   `user_statement`, `kendra` vs `kinda` vs `name`. `exclude_kinds` filtering
   silently misses these. **Not fixed.**

4. **"tell me who you are" routes to face recognition.** `_WHO_QUESTION`
   matches it and takes the 0.3 s YuNet path, which answers *who she sees*.
   Verified: `"tell me who you are"` and `"who am i"` both match; `"who are
   you"` does not. **Not fixed.**

### P2 — CRITICAL — The renderer can never reload

**Symptom.** She looks dead after any rebuild. Reported as "she died" four
separate times today; three of those were this.

**Evidence.** `snapshot #4835` in `logs/desktop-launcher.log`. She polls every
3 s, so that renderer had been up ~4 hours without ever reloading — through
a dozen rebuilds.

**Root cause.** [`main.mjs:301`](../dashboard/electron/main.mjs) calls
`window.removeMenu()`. Electron's default **Cmd+R accelerator lives in the
application menu**, so removing the menu removed the only way to reload.
There was no way to pick up new code short of quitting.

**FIXED, needs an app restart to take effect** — Cmd+R and Cmd+Shift+R are now
bound via `before-input-event`, which survives `removeMenu()`. It is
main-process code, so the running instance does not have it yet.

> If the UI looks stale or dead, **this is the first thing to check.** Confirm
> with the snapshot counter before debugging anything else.

### P3 — OPEN — The animation is unconvincing

Her body is now `dashboard/src/kendraSprite.ts` — a 2D Canvas creature, no
3D, no assets (see §6 for why). It is a real improvement on the 3D route but
the user's critique is correct and unaddressed:

- **Her mouth has nothing to do with her speech.** It is
  `Math.sin(t * 11) * 0.6 + Math.sin(t * 6.3 + 1.1) * 0.4` — invented
  chatter. It should be driven by the actual utterance: either a real
  amplitude envelope published by the voice service, or a syllable envelope
  derived from the reply text and started when speech starts. **The reply
  text and a speech-start timestamp are already in the renderer** — see
  `speechDuration()` in `KendraBody.tsx`. That is the cheap path.
- **Timing is not tied to anything.** Moods ease over a fixed 6/sec constant
  regardless of what changed.
- **Only 11 emotions**, and they are postures, not reactions. There is no
  surprise-then-recover, no thinking-then-realising, no listening-then-
  leaning-in. Conversation has *transitions*; she only has states.
- **No mutation or coloration.** This is the biggest miss. Her colour is a
  single constant `BASE = {r:116, g:191, b:196}` in `kendraSprite.ts`.

**The idea I should have generated and didn't:** drive her colour from
conversation sentiment, and make that the *same signal* that later drives the
Pi's LEDs. One emotional channel, pixels now, physical light on the robot
later. Everything needed already exists — `kendra/leds/` is a running
service, and the sprite has a single `shade()` function every colour flows
through. That is a small change with a large payoff and it is the first thing
I would build next.

### P4 — OPEN — Logs are 615 MB

`logs/` is 615 MB. `llm-server.log` alone was 391 MB; `desktop-launcher.log`
198 MB. Disk is fine (739 GB free) so this is untidy rather than urgent, but
nothing rotates and it grows every session. Note the launcher logs llama.cpp
stdout at `ERROR` level, so the file is mostly routine inference chatter
tagged as errors — which also makes real errors hard to grep.

### P5 — WATCH — The half-duplex law

Nothing may run inference while she is in conversation; port 17800 is her only
speech model. A background wiki-compile loop violated this today and made her
appear to choke (fixed — see §4). When adding any background work, gate it on
conversation idleness the way `kendra/brain/service.py` does.

---

## 4. Uncommitted work (19 paths on `6a74b87`)

All of today. Tests pass and lint is clean, but **none of it is committed** —
decide that first.

**Fixes that are live in the Python services now:**

| File | Change |
|---|---|
| `kendra/brain/consolidator.py` | Wiki-compile could wedge forever: truncated JSON threw, and the early return skipped the cursor advance the code's own comment said was mandatory. Same 20 entries recycled every idle window against her only LLM while `pending` climbed 726→748. Now: unusable answers skip the batch, transport failures keep it, truncated answers are salvaged for whole pages. Plus the P1.2 person fixes. |
| `kendra/voice/audio.py` | VAD floor 120→350. Calibrating in a quiet moment pinned it below the room's real ambient (128–260), so silence never registered and every capture ran to the 25 s cap. Also self-heals — but **only when silence genuinely never registered**; my first version misfired on a trough of 18 vs threshold 550, raised it to 900, and made her deaf. |
| `kendra/agent/planner.py` | `_curb_question_tic` was defined and **called from nowhere** — six consecutive replies each ended in a question. Now wired as `_curb_interview`. Added `_speak_to_him` so she stops narrating him in third person to his face. |
| `kendra/expression/detect.py`, `kendra/phrasing.py` | "Yeah. You want to sing it again?" and "Use your synthesizer." both fell through to chat. Invitations and any verb against the synth now route to the expression engine. `"sing it again"` no longer yields the junk subject `"it again"`. |

**Renderer — replaced 3D with the 2D sprite:**

`dashboard/src/kendraSprite.ts` (new), `KendraBody.tsx`, `globals.css`,
`page.tsx`, `electron/main.mjs` (P2 fix), `dashboard/src/kendraStage.ts`
(deleted → archived).

Bundle **816 KB → 215 KB**; the 9.3 MB GLB fetch before first paint is gone.

**Tests:** `tests/test_performance_invariants.py` (+24),
`tests/test_tripo_install.py` (new), `dashboard/tests/sprite.test.mjs` (new),
two 3D-only test files archived.

**Docs:** `docs/TRIPO_API.md` rewritten — see §6.

---

## 5. Traps that cost me hours

- **Anything that starts her and exits, kills her.** The launcher holds
  Electron in the foreground; when the parent shell's process group is torn
  down, Electron gets `SIGTERM`. I killed her three times this way, including
  with `nohup setsid` and with a detached background task. **A human must
  launch her from their own terminal.**
- **`dev start` does not manage voice.** `python -m kendra ... dev start`
  brings up 9 services. Voice is launched by the Electron app because on
  macOS only the app holds microphone permission. `dev stop`/`dev start`
  silently leaves her with no voice.
- **Stale `runtime/pc/*.sock` files block startup.** A service that died
  without cleanup leaves a socket that the next one cannot bind. Symptom: one
  service missing with no error. `rm -f runtime/pc/*.sock` before a cold start.
- **Verify on the streaming path.** Voice uses
  `_stream_voice_turn_inner`, not `_plain_turn`. A fix applied to the wrong
  one looks correct in tests and changes nothing in her mouth.
- **`ruff check | tail -1` lies.** "No fixes available" is not the pass line;
  `All checks passed!` is. Two real errors reached CI that way.
- **Heredocs that assert mid-script leave half-applied edits.** Prefer
  per-hunk edits with a grep to verify.
- **This IS a git repo.** I said otherwise earlier and was wrong — the
  environment reports that for the parent directory. `kendra/kendra` has
  history.

---

## 6. The 3D saga — do not repeat it

Four attempts, ~7 hours, all abandoned. Recorded so nobody retries them:

1. **Tripo v2 auto-rig** forced a **biped** skeleton onto an eight-legged
   creature (41 humanoid bones) and tore the mesh apart.
2. **A supplied Sketchfab octopus** had an excellent rig (8 clean 5-bone
   chains) but was a stock octopus, not her.
3. **Tripo v3 octopod re-rig** (35 credits, balance 455→420) correctly
   identified her as `octopod` — v3 *does* have non-humanoid riggers, which
   overturns the v2 conclusion. But it produced 6 limb labels with one
   degenerate single bone, and the model carried background artefacts because
   the reference cutout had a 1.0% semi-transparent fringe.
4. **A "hexapod model"** turned out to be 20 STL 3D-printing parts — no
   skeleton, no textures, no assembly data.

Everything is in `archive/3d-attempt/` (241 MB) — both models, the three.js
stage, and the rig tests. `docs/TRIPO_API.md` has the full v3 API notes
including the trap that wasted the most time: **v3 lives on
`openapi.tripo3d.ai`, not `api.tripo3d.ai`** — probing the v2 host 404s
forever and looks like "v3 doesn't exist".

**The lesson:** none of the difficulty was ever about animation. It was about
assets. The 2D sprite has no assets and cannot have this class of problem.

---

## 7. What I would do next, in order

1. **Restart her** so the P2 reload fix lands. Nothing else can be verified
   from the UI until this happens.
2. **Anchor her identity** (P1.1) — the highest-value single edit in the
   codebase right now. Static text in `_conversation_prompt()`.
3. **Retire the corrupt memories** (P1.2, P1.3) — 5 mixed-person rows, 2
   polluted-`kind` rows, and normalise the kind vocabulary.
4. **Fix `_WHO_QUESTION`** (P1.4) so self-identity never routes to the face
   recogniser.
5. **Sentiment → colour → LEDs** (P3). The idea the user had and I missed.
   One signal, `shade()` in the sprite now, `kendra/leds/` on the Pi later.
6. **Real mouth timing** (P3) from the reply text already in the renderer.
7. **Log rotation** (P4).

---

## 8. Where things live

```
kendra/agent/planner.py        her turn logic, guards, routing  (large)
kendra/brain/                  memory: store, consolidator, second brain
kendra/voice/                  audio capture, VAD, TTS, expression triggers
kendra/expression/             singing, poems, games — 17 behaviours
dashboard/src/kendraSprite.ts  her body: 431 lines, no assets
dashboard/src/KendraBody.tsx   mood derivation from her live snapshot
dashboard/electron/main.mjs    window, IPC, CSP, the Cmd+R fix
docs/TRIPO_API.md              Tripo v3 notes (if 3D is ever revisited)
archive/3d-attempt/            everything abandoned, restorable
```

**Verify anything with:**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check kendra/ tests/ scripts/
cd dashboard && npm run build && node --test tests/*.test.mjs
```
