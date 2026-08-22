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
function moodFor({ latestReply = null, moving = false, busy = null, listening = false }) {
  const performance = /^\((sings|hums|plays|dances)/i;
  if (latestReply && performance.test(latestReply.trim())) {
    return /plays|dances/i.test(latestReply) ? "delighted" : "singing";
  }
  if (moving) return "walking";
  if (busy === "sight") return "curious";
  if (busy) return "thinking";
  if (listening) return "listening";
  return "idle";
}

test("singing and humming put her into the sway", () => {
  assert.equal(moodFor({ latestReply: "(sings, lullaby) Over the hill…" }), "singing");
  assert.equal(moodFor({ latestReply: "(hums, contented)" }), "singing");
});

test("playing a tune reads as delight", () => {
  assert.equal(moodFor({ latestReply: "(plays little wander)" }), "delighted");
});

test("real body movement wins over idle", () => {
  assert.equal(moodFor({ moving: true }), "walking");
});

test("her eyes working reads as curiosity, other work as thinking", () => {
  assert.equal(moodFor({ busy: "sight" }), "curious");
  assert.equal(moodFor({ busy: "chat" }), "thinking");
});

test("listening only when nothing else is happening", () => {
  assert.equal(moodFor({ listening: true }), "listening");
  assert.equal(moodFor({ listening: true, busy: "chat" }), "thinking");
});

test("ordinary speech does not trigger a performance", () => {
  assert.equal(moodFor({ latestReply: "I think jazz is mostly improvisation." }), "idle");
  assert.equal(moodFor({ latestReply: "He plays guitar every day." }), "idle");
});
