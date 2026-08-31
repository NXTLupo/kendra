/**
 * Her mouth must be driven by her voice, not by a poll.
 *
 * The bug these exist to prevent: the renderer learned she had spoken only
 * when a three-second snapshot poll delivered a transcript that is written
 * AFTER the reply finishes. Her mouth therefore animated speech that was
 * already over, on a duration guessed at thirteen characters per second, and
 * because `speak()` overwrote a single slot, two replies inside one poll
 * window played as one rushed burst instead of two utterances.
 *
 * These load and run the real class rather than reading its source, so they
 * fail if the behaviour regresses even when the code still looks right.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import { transform } from "esbuild";

/** Load kendraSprite.ts as a real module. */
async function loadSprite() {
  const source = await readFile(new URL("../src/kendraSprite.ts", import.meta.url), "utf8");
  const { code } = await transform(source, { loader: "ts", format: "esm", target: "es2022" });
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}

// Enough browser for the sprite to construct. She only needs a device pixel
// ratio and an animation clock; everything else she draws herself.
globalThis.window ??= { devicePixelRatio: 1, addEventListener() {}, removeEventListener() {} };
globalThis.requestAnimationFrame ??= () => 0;
globalThis.cancelAnimationFrame ??= () => {};

// Her easing is driven by REAL elapsed time, so a tight loop of frame() calls
// advances nothing at all — every interpolation sees dt of about zero. Tests
// that watch her move have to drive the clock themselves.
let clock = 1000;
const realNow = performance.now.bind(performance);
performance.now = () => clock;

/** Run `frames` animation frames at 60fps of simulated time. */
function run(sprite, frames) {
  for (let i = 0; i < frames; i += 1) {
    clock += 16;
    sprite.frame();
  }
}

/** Enough canvas for the sprite to construct and draw into. */
function stubCanvas() {
  const noop = () => {};
  const ctx = new Proxy(
    {
      canvas: null,
      createRadialGradient: () => ({ addColorStop: noop }),
      createLinearGradient: () => ({ addColorStop: noop }),
      measureText: () => ({ width: 10 }),
      getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    },
    { get: (target, key) => (key in target ? target[key] : noop), set: () => true },
  );
  return {
    getContext: () => ctx,
    clientWidth: 320,
    clientHeight: 320,
    width: 320,
    height: 320,
    style: {},
  };
}

async function newSprite() {
  const { KendraSprite } = await loadSprite();
  return new KendraSprite(stubCanvas());
}

test("a second phrase queues behind the first instead of replacing it", async () => {
  const kendra = await newSprite();
  kendra.speak("The first thing she says.", 2.0);
  kendra.speak("And the second thing.", 1.5);

  assert.equal(kendra.speaking, true);
  // The bug: the second call replaced the first, so the first was cut dead
  // and the second replayed from zero. Ending the first must reveal the
  // second still waiting.
  kendra.endSpeech();
  assert.equal(kendra.speaking, true, "the queued phrase must survive the first ending");
  kendra.endSpeech();
  assert.equal(kendra.speaking, false, "the queue must drain, not loop");
});

test("a burst of phrases plays as a queue, not all at once", async () => {
  const kendra = await newSprite();
  for (const line of ["One.", "Two.", "Three.", "Four."]) kendra.speak(line, 0.9);
  let played = 0;
  while (kendra.speaking && played < 10) {
    played += 1;
    kendra.endSpeech();
  }
  assert.equal(played, 4, "every phrase must get its own turn");
});

test("an unknown duration holds until the audio really ends", async () => {
  const kendra = await newSprite();
  // seconds = 0 is what a streaming engine reports: it cannot know its own
  // length yet. The old code would have invented an end time from the text.
  kendra.speak("A sentence whose length nobody knows yet.", 0);
  assert.equal(kendra.speaking, true);

  // Far beyond any character-count estimate for this text.
  const realNow = performance.now;
  try {
    performance.now = () => realNow.call(performance) + 120_000;
    kendra.frame();
    assert.equal(kendra.speaking, true, "she must not stop speaking because an estimate expired");
  } finally {
    performance.now = realNow;
  }
  kendra.endSpeech();
  assert.equal(kendra.speaking, false, "the real end event stops her");
});

test("being interrupted drops the queue, not just the current phrase", async () => {
  const kendra = await newSprite();
  kendra.speak("Something long she was saying.", 4);
  kendra.speak("And more after it.", 4);
  kendra.hush();
  assert.equal(kendra.speaking, false, "barge-in must clear everything waiting");
});

test("an empty or unspeakable phrase never starts her mouth", async () => {
  const kendra = await newSprite();
  kendra.speak("   ", 1);
  assert.equal(kendra.speaking, false);
});

test("the thinking bubble is raised and released by explicit events", async () => {
  const kendra = await newSprite();
  assert.equal(kendra.thinking, null);
  kendra.think("search");
  assert.equal(kendra.thinking.mode, "search");
  // Mode changes in place; the bubble does not restart, because it is one
  // continuous thought that changed character.
  const since = kendra.thinking.since;
  kendra.think("look");
  assert.equal(kendra.thinking.mode, "look");
  assert.equal(kendra.thinking.since, since, "changing mode must not restart the bubble");
  kendra.stopThinking();
  assert.equal(kendra.thinking, null);
});

test("speaking and thinking are exclusive: the bubble drops when she talks", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.think("think");
  // The renderer clears the bubble on speech_start; assert the sprite offers
  // the operation that makes that possible and that it composes.
  kendra.stopThinking();
  kendra.speak("Here is what I found.", 1.2);
  assert.equal(kendra.thinking, null);
  assert.equal(kendra.speaking, true);
});

test("she draws without throwing while speaking and thinking", async () => {
  const kendra = await newSprite();
  kendra.think("search");
  kendra.speak("Drawing this frame.", 1);
  // One real frame through the whole pipeline, bubble included.
  assert.doesNotThrow(() => kendra.frame());
});

test("nothing in the renderer estimates speech timing any more", async () => {
  const body = await readFile(new URL("../src/KendraBody.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(
    body,
    /speechDuration/,
    "speech duration must come from the audio, never from a character count",
  );
  assert.match(body, /speech_start/, "her mouth must be driven by the speech_start event");
  assert.match(body, /onEvent/, "the renderer must subscribe to the face bus");
});

test("the preload bridge exposes the event channel", async () => {
  const preload = await readFile(new URL("../electron/preload.cjs", import.meta.url), "utf8");
  assert.match(preload, /onEvent/);
  assert.match(preload, /kendra:event/);

  const main = await readFile(new URL("../electron/main.mjs", import.meta.url), "utf8");
  // The exact bug: an unsolicited line hit `if (!item) return` and vanished.
  assert.match(main, /if \(message\.event\)/, "event lines must be handled before the id lookup");
  assert.match(main, /webContents\.send\("kendra:event"/);
});

// --- colour must never pass through a hue neither end asked for --------------

test("she does not turn red on her way into a song", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());

  // Delighted (gold, hue 44) into singing (violet, 275) is the transition
  // that did it: the shortest way round the wheel is -129 degrees, which runs
  // 44 -> 0 -> 315, straight through red. Measured on the real easing curve
  // that was eleven frames of rgb(204,62,73) -- peak redness 0.62 -- every
  // time she started to sing after a happy moment.
  kendra.setMood("delighted");
  run(kendra, 200);
  const gold = kendra.emotion();
  assert.ok(Math.abs(gold.hue - 44) < 8, `did not settle on gold: hue ${gold.hue}`);

  kendra.setMood("singing");
  let worst = 0;
  for (let i = 0; i < 150; i += 1) {
    run(kendra, 1);
    const [r, g, b] = kendra.emotion().rgb;
    worst = Math.max(worst, (r - Math.max(g, b)) / 255);
  }
  assert.ok(worst < 0.2, `she went red in transit: peak redness ${worst.toFixed(3)}`);
});

test("colour still arrives where it was going", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.setMood("delighted");
  run(kendra, 200);
  kendra.setMood("singing");
  run(kendra, 400);
  const { hue, sat } = kendra.emotion();
  // singing is hue 275, sat .58 in the feeling table.
  assert.ok(Math.abs(hue - 275) < 12, `settled on hue ${hue}, expected ~275`);
  assert.ok(sat > 0.45, `saturation never recovered: ${sat}`);
});

// --- a wordless performance still moves her mouth ----------------------------

test("humming moves her mouth even though it has no words", async () => {
  const { KendraSprite, sustainedBeats } = await loadSprite();
  const built = sustainedBeats(4);
  assert.ok(built.beats.length >= 4, "a four-second hum needs several beats");
  assert.ok(built.beats.every((b) => b.peak > 0.3), "the mouth must actually open");

  const kendra = new KendraSprite(stubCanvas());
  kendra.speak("", 4, "hum");
  assert.equal(kendra.speaking, true, "a wordless hum is still her voice");
});

test("a sung line with lyrics uses its syllables, not the hum envelope", async () => {
  const { KendraSprite, speechBeats, sustainedBeats } = await loadSprite();
  const sung = speechBeats("Mary had a little lamb");
  const hummed = sustainedBeats(2);
  assert.notEqual(sung.beats.length, hummed.beats.length);

  const kendra = new KendraSprite(stubCanvas());
  kendra.speak("Mary had a little lamb", 2, "song");
  assert.equal(kendra.speaking, true);
});

test("silence is still silence", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.speak("", 0, "hum");
  assert.equal(kendra.speaking, false, "no words and no duration is nothing at all");
});

test.after(() => { performance.now = realNow; });

// --- her legs -----------------------------------------------------------------

test("a walk steps on alternating tripods, at her real gait period", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  // Four cycles at the vendor's real 0.4s tripod period.
  kendra.move("walk", "forward", 4, 0.4, 0.35);
  assert.equal(kendra.moving, true);

  // Sample the two tripods across one cycle. Legs 0,2,4 swing together and
  // 1,3,5 swing together, half a cycle apart — that is what makes it a walk
  // and not a shuffle.
  const lift = (i) => kendra.tripodLift(i);
  const at = (ms) => { clock += ms; };
  let sawA = false, sawB = false, sawOpposed = false;
  for (let i = 0; i < 40; i += 1) {
    at(20);
    const a = lift(0), b = lift(1);
    assert.equal(lift(0), lift(2), "legs 0 and 2 must share a tripod");
    assert.equal(lift(1), lift(3), "legs 1 and 3 must share a tripod");
    if (a > 0.3) sawA = true;
    if (b > 0.3) sawB = true;
    if (a > 0.3 && b === 0) sawOpposed = true;
  }
  assert.ok(sawA && sawB, "both tripods must take a turn");
  assert.ok(sawOpposed, "the tripods must alternate, never lift together");
});

test("a walk ends on its own after the commanded cycles", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.move("walk", "forward", 2, 0.4, 0.35);   // 0.8 seconds of walking
  clock += 400;
  assert.equal(kendra.moving, true, "still stepping mid-walk");
  clock += 900;
  assert.equal(kendra.moving, false, "she must stop when the cycles are done");
});

test("every direction is a distinct movement", async () => {
  const { KendraSprite } = await loadSprite();
  const seen = new Map();
  for (const direction of ["forward", "backward", "left", "right"]) {
    const kendra = new KendraSprite(stubCanvas());
    kendra.setMood("walking");
    run(kendra, 30);
    const before = { x: kendra.bodyX, scale: kendra.walkScale };
    kendra.move("walk", direction, 4, 0.4, 0.35);
    run(kendra, 60);
    seen.set(direction, {
      dx: +(kendra.bodyX - before.x).toFixed(2),
      dscale: +(kendra.walkScale - before.scale).toFixed(3),
    });
  }
  // Sideways moves her across; forward and back change her apparent depth.
  assert.ok(seen.get("left").dx < -1, `left did not move her: ${seen.get("left").dx}`);
  assert.ok(seen.get("right").dx > 1, `right did not move her: ${seen.get("right").dx}`);
  assert.ok(seen.get("forward").dscale > 0.01, "forward must bring her closer");
  assert.ok(seen.get("backward").dscale < -0.01, "backward must take her away");
});

test("turning swings her body and is not a walk", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  run(kendra, 30);
  const before = kendra.bodyX;
  kendra.move("turn", "right", 3, 0.4, 0.3);
  run(kendra, 60);
  assert.ok(kendra.bodyX > before, "a right turn swings her to the right");
});

test("stopping leaves her where the walk put her", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.move("walk", "right", 4, 0.4, 0.35);
  run(kendra, 40);
  const mid = kendra.bodyX;
  kendra.stopMoving();
  run(kendra, 90);
  assert.equal(kendra.moving, false);
  // She holds her ground rather than snapping back to centre.
  assert.ok(Math.abs(kendra.bodyX - mid) < kendra.radius * 0.9,
    "she must not teleport home when the walk ends");
});

test("the research and look states are distinct from each other and from thinking", async () => {
  const { KendraSprite } = await loadSprite();
  const hues = {};
  for (const mood of ["thinking", "researching", "looking"]) {
    const kendra = new KendraSprite(stubCanvas());
    kendra.setMood(mood);
    run(kendra, 300);
    hues[mood] = kendra.emotion().hue;
  }
  const gap = (a, b) => Math.abs(((a - b + 540) % 360) - 180);
  assert.ok(gap(hues.thinking, hues.researching) > 40, `thinking vs researching too close: ${JSON.stringify(hues)}`);
  assert.ok(gap(hues.researching, hues.looking) > 40, `researching vs looking too close: ${JSON.stringify(hues)}`);
  assert.ok(gap(hues.thinking, hues.looking) > 40, `thinking vs looking too close: ${JSON.stringify(hues)}`);
});

test("the magnifying glass is raised only while she is researching", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  run(kendra, 30);
  assert.ok(kendra.hold < 0.05, "no glass at rest");
  kendra.setMood("researching");
  run(kendra, 60);
  assert.ok(kendra.hold > 0.5, `glass should be up, hold=${kendra.hold}`);
  kendra.setMood("idle");
  run(kendra, 120);
  assert.ok(kendra.hold < 0.15, `glass should be down, hold=${kendra.hold}`);
});

test("the camera iris engages only while she is looking", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  run(kendra, 30);
  assert.ok(kendra.iris < 0.05);
  kendra.setMood("looking");
  run(kendra, 40);
  assert.ok(kendra.iris > 0.5, `iris should be engaged, got ${kendra.iris}`);
  kendra.setMood("idle");
  run(kendra, 150);
  assert.ok(kendra.iris < 0.15);
});

test("she draws every new state without throwing", async () => {
  const { KendraSprite } = await loadSprite();
  for (const mood of ["researching", "looking", "walking"]) {
    const kendra = new KendraSprite(stubCanvas());
    kendra.setMood(mood);
    kendra.move("walk", "forward", 4, 0.4, 0.35);
    assert.doesNotThrow(() => run(kendra, 40), `${mood} threw while drawing`);
  }
});

test("the thought bubble is never clipped by the frame", async () => {
  const { KendraSprite } = await loadSprite();
  const canvas = stubCanvas();
  const kendra = new KendraSprite(canvas);
  // Her head sits at 42% of the canvas height, so a bubble at a fixed offset
  // above it ran off the top of the frame and was cut in half.
  kendra.think("think");
  run(kendra, 60);
  const b = kendra.bubbleBox();
  assert.ok(b, "the bubble should be up");
  assert.ok(b.top >= 0, `bubble top is off-screen at ${b.top.toFixed(1)}`);
  assert.ok(b.left >= 0, `bubble runs off the left at ${b.left.toFixed(1)}`);
  assert.ok(b.right <= canvas.clientWidth, `bubble runs off the right at ${b.right.toFixed(1)}`);
});

test("her eyes glance rather than dart", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.setMood("idle");
  run(kendra, 60);
  // Count how far the gaze travels over ten seconds of idling. Darting eyes
  // read as nerves; this is the "jitters" complaint, measured.
  let travel = 0;
  let last = kendra.gaze.x;
  for (let i = 0; i < 600; i += 1) {
    run(kendra, 1);
    travel += Math.abs(kendra.gaze.x - last);
    last = kendra.gaze.x;
  }
  assert.ok(travel < 4, `eyes travelled ${travel.toFixed(2)} eye-widths in 10s — too busy`);
});

test("a routine servo rest is not drawn as alarm", async () => {
  // She finished every walk bright red with a frightened face, because
  // `reflex_lock` is also set while her legs take their normal breather.
  const { readFile } = await import("node:fs/promises");
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const startled = /startled=\{Boolean\(([\s\S]*?)\)\}/.exec(page)?.[1] ?? "";
  assert.match(startled, /reflex_fault/, "alarm must come from a fault");
  assert.doesNotMatch(startled, /reflex_lock/, "a servo rest is not an emergency");
});

test("she actually waves when she greets someone", async () => {
  // `WAVING_TENTACLE` was declared and documented for months and used by
  // nothing, so her greeting had no wave in it. eslint found it.
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.setMood("idle");
  run(kendra, 60);
  const resting = kendra.tentacles[1].points.at(-1).y;

  kendra.setMood("greeting");
  run(kendra, 60);
  let highest = Infinity;
  for (let i = 0; i < 90; i += 1) {
    run(kendra, 1);
    highest = Math.min(highest, kendra.tentacles[1].points.at(-1).y);
  }
  assert.ok(highest < resting - kendra.radius * 0.3,
    `the greeting limb never lifted: ${highest.toFixed(1)} vs resting ${resting.toFixed(1)}`);

  const source = await readFile(new URL("../src/kendraSprite.ts", import.meta.url), "utf8");
  assert.match(source, /index === WAVING_TENTACLE/, "the constant must actually be used");
});

test("total deafness is drawn, not hidden", async () => {
  // An unauthorized microphone on macOS opens fine and returns zeros forever,
  // so a deaf Kendra was pixel-identical to a waiting one. That is exactly
  // what produced "she's dead and not reacting at all".
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.setDeaf(true);
  run(kendra, 30);
  assert.doesNotThrow(() => run(kendra, 10), "the deaf badge must draw cleanly");
  kendra.setDeaf(false);
  run(kendra, 10);

  const body = await readFile(new URL("../src/KendraBody.tsx", import.meta.url), "utf8");
  assert.match(body, /case "deaf"/, "the renderer must handle the deaf event");
  assert.match(body, /setDeaf/, "and pass it to the sprite");
});

test("a mood change never lurches her body", async () => {
  // THE JITTER. Settled she moves 0.18 px per frame; a mood change used to
  // move her 14 px in ONE frame, because the reaction overshoot was applied
  // to her POSITION. Moods change several times per turn now that listening,
  // thinking, speaking and idle all arrive as events, so that lurch repeated
  // is what reads as "she has the jitters".
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  kendra.setMood("idle");
  run(kendra, 200);

  const worst = (drive, frames) => {
    let previous = null;
    let peak = 0;
    for (let i = 0; i < frames; i += 1) {
      drive(i);
      run(kendra, 1);
      if (previous) {
        peak = Math.max(peak, Math.hypot(kendra.bodyX - previous.x, kendra.bodyY - previous.y));
      }
      previous = { x: kendra.bodyX, y: kendra.bodyY };
    }
    return peak;
  };

  assert.ok(worst(() => {}, 120) < 1, "settled, she should barely move");

  const moods = ["listening", "thinking", "talking", "idle", "researching", "looking"];
  const churn = worst((i) => { if (i % 10 === 0) kendra.setMood(moods[(i / 10) % moods.length]); }, 300);
  assert.ok(churn < kendra.radius * 0.12,
    `mood churn lurched her ${churn.toFixed(1)} px (radius ${kendra.radius.toFixed(0)})`);
});

test("the speed limit does not stop her actually walking", async () => {
  const { KendraSprite } = await loadSprite();
  const kendra = new KendraSprite(stubCanvas());
  run(kendra, 60);
  const before = kendra.bodyX;
  kendra.move("walk", "right", 6, 0.4, 0.4);
  run(kendra, 120);
  assert.ok(kendra.bodyX - before > kendra.radius * 0.3,
    "a commanded walk must still carry her across");
});
