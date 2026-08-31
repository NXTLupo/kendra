/**
 * The animation triggering, tested without a GPU or a model file.
 *
 * These lock the mood mapping — the part that decides what she DOES when
 * something happens in conversation — so it survives whichever model ends
 * up installed.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

/** Mirrors the logic in src/KendraBody.tsx. */
function moodFor({
  latestReply = null, moving = false, running = false, speaking = false,
  performing = false, thinkingMode = null, micOpen = false,
  busy = null, listening = false, startled = false,
}) {
  const performanceRe = /^\((sings|hums|plays|dances)/i;
  const greeting = /^\s*(?:\(\w+\)\s*)?(hi\b|hey\b|hello\b|good (?:morning|afternoon|evening)\b|nice to meet you\b|i'?m kendra\b|my name is kendra\b)/i;
  if (performing || (speaking && latestReply && performanceRe.test(latestReply.trim()))) {
    return /plays|dances/i.test(latestReply ?? "") ? "delighted" : "singing";
  }
  if (startled) return "startled";
  if (speaking) return greeting.test(latestReply ?? "") ? "greeting" : "talking";
  // Her voice service separates the three kinds of work she does, so each
  // gets its own read instead of all of them collapsing into "busy".
  if (thinkingMode) {
    return thinkingMode === "look" ? "looking"
      : thinkingMode === "search" ? "researching"
      : thinkingMode === "curious" ? "curious"
      : "thinking";
  }
  if (micOpen) return "listening";
  if (moving) return running ? "running" : "walking";
  if (busy === "sight") return "looking";
  if (busy === "research") return "researching";
  if (busy) return "thinking";
  if (listening) return "listening";
  return "idle";
}



test("singing and humming put her into the sway", () => {
  // `performing` comes from a live speech_start event carrying kind=song|hum,
  // published as the audio starts. It used to be inferred from a "(sings"
  // marker in the transcript -- which her brain writes only AFTER she has
  // finished singing, so the sway arrived once the song was over.
  assert.equal(moodFor({ performing: true, speaking: true, latestReply: "(sings, lullaby) Over the hill…" }), "singing");
  assert.equal(moodFor({ performing: true, speaking: true, latestReply: "(hums, contented)" }), "singing");
});

test("a performance marker alone is not enough — the audio has to be playing", () => {
  // The transcript is written after the fact. Acting on it would replay the
  // sway long after she stopped singing.
  assert.equal(moodFor({ latestReply: "(sings, lullaby) Over the hill…" }), "idle");
});

test("playing a tune reads as delight", () => {
  assert.equal(moodFor({ performing: true, speaking: true, latestReply: "(plays little wander)" }), "delighted");
});

test("real body movement wins over idle", () => {
  assert.equal(moodFor({ moving: true }), "walking");
});

test("each kind of work she does has its own read", () => {
  // Her voice service already labels these three; collapsing them into one
  // "busy" state threw away information she had already computed.
  assert.equal(moodFor({ thinkingMode: "look" }), "looking");
  assert.equal(moodFor({ thinkingMode: "search" }), "researching");
  assert.equal(moodFor({ thinkingMode: "think" }), "thinking");
  assert.equal(moodFor({ busy: "sight" }), "looking");
  assert.equal(moodFor({ busy: "chat" }), "thinking");
});

test("looking, researching and thinking are three different states", () => {
  const states = new Set([
    moodFor({ thinkingMode: "look" }),
    moodFor({ thinkingMode: "search" }),
    moodFor({ thinkingMode: "think" }),
  ]);
  assert.equal(states.size, 3, "each must be visually distinct, not aliases");
});

test("noticing something unprompted is not the same as being told to look", () => {
  // Ambient curiosity comes from her vision service; "take a look" comes from
  // her voice service. They had been collapsed into one picture.
  assert.equal(moodFor({ thinkingMode: "curious" }), "curious");
  assert.notEqual(moodFor({ thinkingMode: "curious" }), moodFor({ thinkingMode: "look" }));
});

test("listening only when nothing else is happening", () => {
  assert.equal(moodFor({ listening: true }), "listening");
  assert.equal(moodFor({ listening: true, busy: "chat" }), "thinking");
});

test("ordinary speech does not trigger a performance", () => {
  assert.equal(moodFor({ latestReply: "I think jazz is mostly improvisation." }), "idle");
  assert.equal(moodFor({ latestReply: "He plays guitar every day." }), "idle");
});


test("she gestures while she speaks", () => {
  // `talking` carries her widest gestures and nothing ever selected it, so
  // she stood frozen through every reply — the dangling dead arms.
  assert.equal(moodFor({ speaking: true, latestReply: "The sky is clear tonight." }), "talking");
  // Speaking outranks the work that produced the words.
  assert.equal(moodFor({ speaking: true, busy: "voice", latestReply: "Here you go." }), "talking");
});

test("her greeting ritual has its own body language", () => {
  for (const line of [
    "Hi there — I don't think we've met.",
    "Hello! I'm Kendra.",
    "Good morning, Jonathan.",
    "Nice to meet you. What should I call you?",
  ]) {
    assert.equal(moodFor({ speaking: true, latestReply: line }), "greeting", line);
  }
  // An ordinary reply is talking, not greeting.
  assert.equal(
    moodFor({ speaking: true, latestReply: "The kettle finished a minute ago." }), "talking",
  );
});

test("a reflex outranks everything — she reacts before she thinks", () => {
  assert.equal(moodFor({ startled: true }), "startled");
  assert.equal(moodFor({ startled: true, speaking: true, latestReply: "Hello" }), "startled");
  assert.equal(moodFor({ startled: true, busy: "sight", moving: true }), "startled");
  // ...but a performance actually playing is not interrupted by a stale
  // reflex flag: she finishes the song.
  assert.equal(moodFor({ startled: true, performing: true, speaking: true, latestReply: "(sings) la la" }), "singing");
});

test("covering real ground reads as running", () => {
  assert.equal(moodFor({ moving: true, running: true }), "running");
  assert.equal(moodFor({ moving: true, running: false }), "walking");
});

test("nothing estimates how long she will be speaking any more", async () => {
  // This used to assert the bounds of a 13-characters-per-second guess. That
  // guess WAS the desync: her mouth ran on it while the audio ran on itself.
  // Speech is now timed by the synthesized buffer's own length.
  const { readFile } = await import("node:fs/promises");
  const body = await readFile(new URL("../src/KendraBody.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(body, /speechDuration/);
  assert.match(body, /speech_start/);
});

test("every feeling she has can actually be reached", async () => {
  // The gap this closes: `talking`, `running`, `startled` and `greeting`
  // were all defined with tuned motion and never selected by anything.
  const { readFile } = await import("node:fs/promises");
  const stage = await readFile(new URL("../src/kendraSprite.ts", import.meta.url), "utf8");
  const declared = [...stage.matchAll(/^  ([a-z]+): +\{ lift:/gm)].map((m) => m[1]);
  assert.ok(declared.length >= 13, `expected the full mood table, saw ${declared.length}`);

  const reachable = new Set([
    moodFor({}),
    moodFor({ listening: true }),
    moodFor({ busy: "chat" }),
    moodFor({ busy: "sight" }),
    moodFor({ thinkingMode: "search" }),
    moodFor({ thinkingMode: "look" }),
    moodFor({ thinkingMode: "think" }),
    moodFor({ thinkingMode: "curious" }),
    moodFor({ micOpen: true }),
    moodFor({ moving: true }),
    moodFor({ moving: true, running: true }),
    moodFor({ speaking: true, latestReply: "Sure." }),
    moodFor({ speaking: true, latestReply: "Hi there!" }),
    moodFor({ startled: true }),
    moodFor({ speaking: true, performing: true, latestReply: "(sings) la" }),
    moodFor({ speaking: true, performing: true, latestReply: "(plays) la" }),
  ]);
  for (const mood of declared) {
    assert.ok(reachable.has(mood), `${mood} is defined but nothing can trigger it`);
  }
});
