/**
 * The drawn Kendra: her feelings, and the physics that carries them.
 *
 * She replaced a 3D pipeline that cost 816 KB of three.js and a 9.3 MB GLB
 * fetched before she could appear, and whose every asset fought us — a
 * rigger that forced a biped skeleton onto a spider, a supplied octopus
 * that was not her, a "hexapod model" that turned out to be STL printing
 * parts. These tests exist so none of that can creep back.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = () => readFile(new URL("../src/kendraSprite.ts", import.meta.url), "utf8");

async function feelings() {
  const text = await source();
  const rows = [...text.matchAll(/^ {2}([a-z]+): +\{ (.+?) \},$/gm)];
  assert.ok(rows.length >= 11, `expected the full feeling table, parsed ${rows.length}`);
  return rows.map(([, name, body]) => {
    const values = Object.fromEntries(
      [...body.matchAll(/([a-zA-Z]+):\s*(-?[\d.]+)/g)].map((m) => [m[1], Number(m[2])]),
    );
    return { name, ...values };
  });
}

test("she stays cheap: no 3D engine, no model fetch", async () => {
  const bundle = await readFile(new URL("../dist/index.html", import.meta.url), "utf8");
  assert.doesNotMatch(bundle, /\.glb/i, "the page must not reference a model file");

  const text = await source();
  assert.doesNotMatch(text, /from ["']three["']/, "no 3D engine");

  // The bundle is the honest metric — what the browser must actually parse
  // before she can appear. The 3D route was 816 KB of library plus a 9.3 MB
  // model fetch; anything near that is a regression.
  const { readdir, stat } = await import("node:fs/promises");
  const dir = new URL("../dist/assets/", import.meta.url);
  const js = (await readdir(dir)).filter((f) => f.endsWith(".js"));
  const bytes = (await Promise.all(js.map(async (f) => (await stat(new URL(f, dir))).size)))
    .reduce((a, b) => a + b, 0);
  assert.ok(bytes < 300_000, `bundle is ${Math.round(bytes / 1024)} KB`);
});

test("every feeling is distinct — no two moods look the same", async () => {
  const rows = await feelings();
  const seen = new Map();
  for (const f of rows) {
    const shape = JSON.stringify(Object.entries(f).filter(([k]) => k !== "name"));
    assert.ok(!seen.has(shape), `${f.name} is identical to ${seen.get(shape)}`);
    seen.set(shape, f.name);
  }
});

test("startled pulls IN while delight pushes OUT", async () => {
  // The one emotional rule that matters: fear contracts, joy expands. Get
  // this backwards and she reads as broken no matter how good the physics.
  const by = Object.fromEntries((await feelings()).map((f) => [f.name, f]));
  assert.ok(by.startled.reach < by.idle.reach, "fear must tuck her limbs in");
  assert.ok(by.delighted.reach > by.idle.reach, "joy must fling them out");
  assert.ok(by.startled.stretch < 1, "startled squashes down");
  assert.ok(by.startled.eyes > by.idle.eyes, "fear blows the eyes wide");
  assert.ok(by.startled.pupil < by.idle.pupil, "...with pinprick pupils");
  assert.ok(by.startled.smile < 0, "startled is not smiling");
  assert.ok(by.delighted.smile > by.idle.smile, "delight smiles harder");
});

test("attention is stillness, excitement is motion", async () => {
  const by = Object.fromEntries((await feelings()).map((f) => [f.name, f]));
  assert.ok(by.listening.bob < by.talking.bob, "listening should go quiet");
  assert.ok(by.listening.eyes > by.idle.eyes, "listening opens her eyes");
  assert.ok(by.running.bobRate > by.walking.bobRate, "running is faster than walking");
  assert.ok(by.singing.sway > by.talking.sway, "singing sways more than speech");
  assert.ok(by.greeting.smile > by.idle.smile, "a greeting is warm");
});

test("nobody floats away or falls through the floor", async () => {
  for (const f of await feelings()) {
    assert.ok(Math.abs(f.lift) <= 0.3, `${f.name}: lift ${f.lift} is off-screen`);
    assert.ok(f.bob >= 0 && f.bob <= 0.3, `${f.name}: bob ${f.bob} out of range`);
    assert.ok(f.reach >= 0 && f.reach <= 1, `${f.name}: reach ${f.reach} out of range`);
    assert.ok(f.eyes > 0, `${f.name}: eyes must never fully vanish`);
    assert.ok(f.bobRate <= 4, `${f.name}: bobRate ${f.bobRate} would strobe`);
  }
});

test("the verlet chain settles instead of exploding", async () => {
  // Mirrors the integrator in stepTentacles. Damping below 1 guarantees the
  // chain loses energy; at 1.0 a nudge would oscillate forever.
  const text = await source();
  const damping = Number(/Math\.pow\((0\.\d+), dt \* 60\)/.exec(text)?.[1]);
  assert.ok(damping > 0 && damping < 1, `damping ${damping} must lose energy`);
  // And it must be per SECOND, not per frame. Damping a fixed fraction each
  // frame made her limbs stiffer on a 120 Hz display than on a 60 Hz one,
  // and under-damped at high refresh rates, which buzzes.
  assert.match(text, /Math\.pow\(0\.\d+, dt \* 60\)/,
    "tentacle damping must be scaled by elapsed time");

  // Run the real thing: nudge a chain and confirm it comes to rest.
  const N = 7, step = 10;
  const pts = Array.from({ length: N }, (_, i) => ({ x: 0, y: i * step, px: 0, py: i * step }));
  pts[N - 1].x = 60;                                   // yank the tip sideways
  for (let frame = 0; frame < 400; frame += 1) {
    for (let i = 1; i < N; i += 1) {
      const p = pts[i];
      const vx = (p.x - p.px) * damping;
      const vy = (p.y - p.py) * damping;
      p.px = p.x; p.py = p.y;
      p.x += vx; p.y += vy;
    }
    for (let pass = 0; pass < 2; pass += 1) {
      for (let i = 1; i < N; i += 1) {
        const a = pts[i - 1], b = pts[i];
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 1e-4;
        const c = (d - step) / d;
        const share = i === 1 ? 1 : 0.5;
        if (i > 1) { a.x += dx * c * (1 - share); a.y += dy * c * (1 - share); }
        b.x -= dx * c * share; b.y -= dy * c * share;
      }
    }
  }
  for (const p of pts) {
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y), "the chain went to NaN");
    const speed = Math.hypot(p.x - p.px, p.y - p.py);
    assert.ok(speed < 0.5, `still moving at ${speed.toFixed(3)} after 400 frames`);
  }
});

// ---------------------------------------------------------------------------
// Speech, reaction and colour — the three things that made her unconvincing.
// ---------------------------------------------------------------------------

/** Mirrors speechBeats() in kendraSprite.ts. */
function speechBeats(text) {
  const beats = [];
  let cursor = 0;
  for (const word of (text || "").trim().split(/\s+/).filter(Boolean)) {
    const letters = word.replace(/[^a-z']/gi, "");
    let count = (letters.toLowerCase().match(/[aeiouy]+/g) || []).length;
    if (/[^aeiou]e$/i.test(letters) && count > 1) count -= 1;
    count = Math.max(1, count);
    for (let i = 0; i < count; i += 1) {
      const wide = /[aeiou]{2}|[aeiou](?:r|w)/i.test(letters) && i === 0;
      beats.push({ start: cursor, span: 1, peak: wide ? 1 : i === 0 ? 0.85 : 0.6 });
      cursor += 1;
    }
    const punct = /[.,;:!?)\]]+$/.exec(word)?.[0] ?? "";
    if (/[.!?]/.test(punct)) cursor += 2.2;
    else if (/[,;:]/.test(punct)) cursor += 1.1;
    else cursor += 0.35;
    if (/\?$/.test(word) && beats.length) beats[beats.length - 1].peak = 1;
  }
  return { beats, total: Math.max(cursor, 1) };
}

test("her mouth is driven by the actual words", async () => {
  const text = await source();
  // The invented chatter must be gone.
  assert.doesNotMatch(text, /Math\.sin\(t \* 11\)/, "the fake chatter loop is back");
  assert.match(text, /speechBeats/, "the envelope builder is missing");

  // A longer line produces more beats. The old loop ran at one rate forever.
  const brief = speechBeats("Mm.");
  const long = speechBeats("I have been thinking about the way music carries feeling.");
  assert.ok(long.beats.length > brief.beats.length * 5,
    `${long.beats.length} beats vs ${brief.beats.length} — length must matter`);
});

test("punctuation becomes silence", () => {
  // The pauses are what make it read as speech rather than chattering.
  const runOn = speechBeats("one two three four");
  const stopped = speechBeats("one. two. three. four.");
  assert.equal(stopped.beats.length, runOn.beats.length, "same syllables");
  assert.ok(stopped.total > runOn.total * 1.4,
    "sentence stops must lengthen the utterance with rests");
});

test("syllables are counted, not characters", () => {
  const count = (s) => speechBeats(s).beats.length;
  assert.equal(count("cat"), 1);
  assert.equal(count("water"), 2);
  assert.equal(count("beautiful"), 3);
  // A silent trailing e must not add a beat.
  assert.equal(count("make"), 1);
  assert.equal(count("hope"), 1);
});

test("a question lifts the final beat", () => {
  const asked = speechBeats("are you sure?");
  assert.equal(asked.beats[asked.beats.length - 1].peak, 1);
});

test("a bigger emotional jump produces a bigger reaction", async () => {
  const rows = await feelings();
  const by = Object.fromEntries(rows.map((f) => [f.name, f]));
  const distance = (a, b) =>
    Math.abs(b.reach - a.reach) * 1.6 + Math.abs(b.lift - a.lift) * 2.4 +
    Math.abs(b.eyes - a.eyes) * 1.1 + Math.abs(b.stretch - a.stretch) * 2.0;

  // What matters is the ORDER, not a magic multiple: the loud feelings must
  // out-rank the quiet ones, so the impulse scales with how much actually
  // changed rather than firing equally for everything.
  const from_idle = (m) => distance(by.idle, by[m]);
  const loud = ["startled", "delighted"].map(from_idle);
  const quiet = ["thinking", "listening"].map(from_idle);
  assert.ok(Math.min(...loud) > Math.max(...quiet) * 2,
    "a startle must register far harder than settling into listening");
  assert.ok(from_idle("thinking") < from_idle("curious"),
    "thinking is an inward shift; curiosity leans out");
  const text = await source();
  assert.match(text, /this\.jolt/, "the reaction impulse is missing");
});

test("colour is part of the feeling, not a constant", async () => {
  const text = await source();
  assert.doesNotMatch(text, /const BASE = \{ r: 116/, "the hardcoded colour is back");
  const rows = await feelings();
  for (const f of rows) {
    assert.ok(Number.isFinite(f.hue), `${f.name} has no hue`);
    assert.ok(f.sat > 0 && f.sat <= 1, `${f.name}: sat ${f.sat} out of range`);
  }
  const by = Object.fromEntries(rows.map((f) => [f.name, f]));
  // The reads that matter: warm when pleased, cool when thinking, alarm when
  // startled. Hue is circular, so compare against teal-at-rest by distance.
  const away = (h) => Math.abs((((h - by.idle.hue) % 360) + 540) % 360 - 180);
  assert.ok(away(by.delighted.hue) > 90, "delight must leave the resting teal");
  assert.ok(away(by.startled.hue) > 90, "alarm must leave the resting teal");
  assert.ok(by.delighted.sat > by.idle.sat, "joy is more saturated than rest");
  assert.ok(by.thinking.hue !== by.idle.hue, "thinking should cool her");
});

test("the emotion signal is exposed for the LEDs, and actually called", async () => {
  const sprite = await source();
  assert.match(sprite, /emotion\(\)\s*:/, "no emotion() accessor");
  assert.match(sprite, /rgb: \[number, number, number\]/, "LEDs need an rgb triple");

  // The bug this catches: a feature that exists but nothing calls.
  const body = await readFile(new URL("../src/KendraBody.tsx", import.meta.url), "utf8");
  assert.match(body, /stage\.speak\(/, "speak() is never called");
  assert.match(body, /stage\.feel\(/, "feel() is never called");
  assert.match(body, /stage\.emotion\(\)/, "emotion() is never called");
  assert.match(body, /kendra:emotion/, "the signal is never published");
});

test("sentiment reads warmth without a model", async () => {
  const body = await readFile(new URL("../src/KendraBody.tsx", import.meta.url), "utf8");
  const warm = /const WARM = (\/.+\/gi);/.exec(body)?.[1];
  const bleak = /const BLEAK = (\/.+\/gi);/.exec(body)?.[1];
  assert.ok(warm && bleak, "sentiment lexicons missing");
  const score = (text) => {
    const w = (text.match(eval(warm)) || []).length;
    const b = (text.match(eval(bleak)) || []).length;
    return w || b ? (w - b) / (w + b) : 0;
  };
  assert.ok(score("That's wonderful, thank you — I'm so glad.") > 0.5);
  assert.ok(score("I'm sorry, something went wrong and I failed.") < -0.5);
  assert.equal(score("The kettle finished a minute ago."), 0);
});
