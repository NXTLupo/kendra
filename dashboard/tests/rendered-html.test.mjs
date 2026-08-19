import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

test("builds a self-contained Kendra desktop renderer", async () => {
  const [html, packageJson] = await Promise.all([
    readFile(new URL("../dist/index.html", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  assert.match(html, /<title>Kendra · Local Companion<\/title>/i);
  assert.match(html, /Content-Security-Policy/i);
  assert.match(html, /assets\/index-/i);
  assert.match(packageJson, /"main": "electron\/main\.mjs"/);
  assert.doesNotMatch(packageJson, /next|vinext|wrangler|cloudflare/i);
});

test("uses sandboxed native IPC with no dashboard HTTP API", async () => {
  const [page, css, main, preload, avatar] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../electron/main.mjs", import.meta.url), "utf8"),
    readFile(new URL("../electron/preload.cjs", import.meta.url), "utf8"),
    stat(new URL("../public/kendra-reference.png", import.meta.url)),
  ]);
  assert.match(page, /window\.kendra/);
  assert.doesNotMatch(page, /fetch\(|\/api\/|8766/);
  assert.match(page, /Talk with Kendra/);
  assert.match(page, /Use my webcam/);
  assert.match(page, /Retrieve memories now/);
  assert.match(page, /config\/webots\.yaml/);
  assert.match(page, /kendra-reference\.png/);
  assert.match(main, /dashboard-bridge/);
  assert.match(main, /contextIsolation: true/);
  assert.match(main, /sandbox: true/);
  assert.match(preload, /contextBridge\.exposeInMainWorld/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /@keyframes companion-breathe/);
  assert.ok(avatar.size > 100_000);
});
