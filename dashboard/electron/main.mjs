import { app, BrowserWindow, ipcMain, session, shell } from "electron";
import { execFile, execSync, spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dashboardRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(dashboardRoot, "..");
const python = process.env.KENDRA_PYTHON || path.join(projectRoot, ".venv", "bin", "python");
const config = process.env.KENDRA_CONFIG || "config/pc.yaml";
const rendererUrl = process.env.KENDRA_RENDERER_URL;
const allowedCommands = new Set([
  "snapshot", "chat", "voice_begin", "voice_end", "voice_audio", "listen", "observe", "vision_frame", "body",
  "memories", "memory_import", "memory_sync", "memory_backup", "update_check",
  "update_request", "photo",
]);

// ---------------------------------------------------------------------------
// Logging.
//
// A double-clicked app has no terminal, and renderer errors would otherwise
// only exist in a devtools console nobody has open. Everything -- main process,
// renderer console, Python bridge stderr, and every IPC command with its
// timing and failure -- lands in one local file so a bad voice turn can be
// diagnosed after the fact.
// ---------------------------------------------------------------------------
const logDir = path.join(projectRoot, "logs", "desktop");
const logPath = path.join(logDir, "kendra-desktop.log");
const MAX_LOG_BYTES = 8 * 1024 * 1024;
let logStream;

function openLog() {
  try {
    fs.mkdirSync(logDir, { recursive: true });
    if (fs.existsSync(logPath) && fs.statSync(logPath).size > MAX_LOG_BYTES) {
      fs.renameSync(logPath, `${logPath}.1`);
    }
    logStream = fs.createWriteStream(logPath, { flags: "a" });
  } catch (error) {
    process.stderr.write(`Kendra could not open its desktop log: ${error.message}\n`);
  }
}

function write(level, source, message) {
  const line = `${new Date().toISOString()} ${level.padEnd(5)} [${source}] ${message}`;
  try {
    logStream?.write(`${line}\n`);
  } catch {
    // Never let logging failures break the app.
  }
  process.stdout.write(`${line}\n`);
}

function describe(value) {
  if (value instanceof Error) return `${value.name}: ${value.message}${value.stack ? `\n${value.stack}` : ""}`;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

const log = {
  info: (source, ...parts) => write("INFO", source, parts.map(describe).join(" ")),
  warn: (source, ...parts) => write("WARN", source, parts.map(describe).join(" ")),
  error: (source, ...parts) => write("ERROR", source, parts.map(describe).join(" ")),
};

openLog();
// Anything already written with console.* should reach the file too.
console.info = (...parts) => log.info("main", ...parts);
console.warn = (...parts) => log.warn("main", ...parts);
console.error = (...parts) => log.error("main", ...parts);
console.log = (...parts) => log.info("main", ...parts);

process.on("uncaughtException", (error) => log.error("main", "uncaught exception", error));
process.on("unhandledRejection", (reason) => log.error("main", "unhandled rejection", reason));

let bridge;
let nextId = 1;
const pending = new Map();
const ownedModels = [];
let quitting = false;
let bridgeRestartTimer;

const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) app.quit();

function rejectPending(message) {
  for (const { reject, timer } of pending.values()) {
    clearTimeout(timer);
    reject(new Error(message));
  }
  pending.clear();
}

function startBridge() {
  if (quitting || (bridge && bridge.exitCode == null && !bridge.killed)) return;
  bridge = spawn(python, ["-m", "kendra", "--config", config, "dashboard-bridge"], {
    cwd: projectRoot,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const lines = createInterface({ input: bridge.stdout });
  lines.on("line", (line) => {
    try {
      const message = JSON.parse(line);
      const item = pending.get(message.id);
      if (!item) return;
      pending.delete(message.id);
      clearTimeout(item.timer);
      if (message.ok) item.resolve(message.result);
      else item.reject(new Error(message.error || "Kendra desktop command failed"));
    } catch (error) {
      console.error("Invalid Kendra bridge response", line.slice(0, 500), error);
    }
  });
  log.info("bridge", `started pid=${bridge.pid} config=${config}`);
  bridge.stderr.on("data", (chunk) => {
    // Python's logging writes to stderr at every level, so classify by content
    // instead of by stream. Otherwise routine health polls read as errors and
    // the log is useless for finding real ones.
    for (const text of String(chunk).split("\n").map((line) => line.trimEnd()).filter(Boolean)) {
      if (/\b(ERROR|CRITICAL|Traceback)\b/.test(text)) log.error("bridge", text);
      else if (/\bWARNING\b/.test(text)) log.warn("bridge", text);
      else log.info("bridge", text);
    }
  });
  bridge.on("error", (error) => {
    log.error("bridge", "could not start", error);
    rejectPending(`Could not start Kendra's desktop bridge: ${error.message}`);
  });
  bridge.on("exit", (code) => {
    bridge = undefined;
    log.error("bridge", `exited with code ${code ?? "unknown"}`);
    rejectPending(`Kendra's desktop bridge stopped (${code ?? "unknown"})`);
    if (!quitting) bridgeRestartTimer = setTimeout(startBridge, 1_000);
  });
}

function localHealth(port, timeoutMs = 2_000) {
  return new Promise((resolve) => {
    const request = http.get({ host: "127.0.0.1", port, path: "/health", timeout: timeoutMs }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on("timeout", () => { request.destroy(); resolve(false); });
    request.on("error", () => resolve(false));
  });
}

async function waitForHealth(port, timeoutMs = 180_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await localHealth(port, 3_000)) return;
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error(`Local model on port ${port} did not become ready`);
}

async function ensureModel(script, port) {
  if (await localHealth(port)) return;
  const child = spawn(path.join(projectRoot, "scripts", script), [], {
    cwd: projectRoot,
    env: { ...process.env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  ownedModels.push(child);
  child.stdout.on("data", (chunk) => console.info(String(chunk).trimEnd()));
  child.stderr.on("data", (chunk) => console.error(String(chunk).trimEnd()));
  await waitForHealth(port);
}

function runPython(args, timeout = 30_000) {
  return new Promise((resolve, reject) => {
    execFile(python, args, { cwd: projectRoot, timeout, maxBuffer: 4 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) reject(new Error(String(stderr || stdout || error.message).trim()));
      else resolve(stdout);
    });
  });
}

async function ensureServices() {
  const base = ["-m", "kendra", "--config", config, "dev"];
  let status = { services: {} };
  try {
    status = JSON.parse(await runPython([...base, "status"]));
  } catch (error) {
    console.error("Could not inspect Kendra services", error);
  }
  // Kendra's nine core services plus voice. Compare against the expected names
  // rather than a bare count so adding an optional service (autonomy) does not
  // silently restart a perfectly healthy stack on every launch.
  const required = ["brain", "identity", "reflex", "body", "research", "vision", "leds", "delivery", "agent", "voice"];
  const services = status.services || {};
  if (required.every((name) => services[name]?.alive)) return;
  if (Object.values(services).some((service) => service.alive)) {
    await runPython([...base, "stop"], 45_000);
  }
  await runPython([...base, "start", "--voice"], 90_000);
}

// First inference after a vision-model start compiles its compute graph and
// can take a minute; pay that cost now with a tiny image instead of during
// the user's first "take a look".
function warmVisionModel() {
  const pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
  const body = JSON.stringify({
    model: "warmup",
    messages: [{ role: "user", content: [
      { type: "text", text: "one word" },
      { type: "image_url", image_url: { url: `data:image/png;base64,${pixel}` } },
    ] }],
    max_tokens: 2,
  });
  const request = http.request(
    { host: "127.0.0.1", port: 17801, path: "/v1/chat/completions", method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) }, timeout: 180_000 },
    (response) => { response.resume(); log.info("runtime", "vision model graph warmed"); },
  );
  request.on("error", () => undefined);
  request.on("timeout", () => request.destroy());
  request.write(body); request.end();
}

async function ensureRuntime() {
  // The semantic vision model is 2.6 GB resident and optional by design.
  // Auto-starting it alongside the brain, ten services, and this window
  // pushed a 16 GB iMac deep into swap and destroyed voice latency, so it is
  // opt-in: set KENDRA_START_VLM=1 or run scripts/start_vlm_intel_macos.sh.
  const jobs = [
    ensureModel("start_llm_intel_macos.sh", 17800),
    ensureModel("start_asr_intel_macos.sh", 17802),
  ];
  // The Q4 brain freed ~2.3 GB, so her semantic eyes fit in memory again.
  // Set KENDRA_START_VLM=0 to keep the vision model off.
  if (process.env.KENDRA_START_VLM !== "0") jobs.push(ensureModel("start_vlm_intel_macos.sh", 17801));
  const [llm, vlm] = await Promise.allSettled(jobs);
  if (llm.status === "rejected") throw llm.reason;
  if (vlm && vlm.status === "rejected") console.error("Kendra's optional semantic vision model is unavailable", vlm.reason);
  else warmVisionModel();
  await ensureServices();
}

function bridgeRequest(method, params = {}) {
  if (!allowedCommands.has(method)) return Promise.reject(new Error("Unsupported Kendra desktop command"));
  if (!bridge || !bridge.stdin.writable) return Promise.reject(new Error("Kendra's local desktop bridge is offline"));
  const id = nextId++;
  const timeout = method === "voice_audio" || method === "listen" ? 600_000 : method === "chat" ? 360_000 : 120_000;
  const started = Date.now();
  // Never log raw params: they carry microphone audio and camera frames.
  log.info("ipc", `-> ${method} #${id}`);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      log.error("ipc", `!! ${method} #${id} timed out after ${timeout}ms`);
      reject(new Error(`${method} timed out locally`));
    }, timeout);
    pending.set(id, {
      resolve: (value) => {
        log.info("ipc", `<- ${method} #${id} ok in ${Date.now() - started}ms`);
        resolve(value);
      },
      reject: (error) => {
        log.error("ipc", `<- ${method} #${id} failed in ${Date.now() - started}ms: ${error.message}`);
        reject(error);
      },
      timer,
    });
    bridge.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
  });
}

function trustedRenderer(frame) {
  if (!frame) return false;
  const url = frame.url;
  return url.startsWith("file:") || url.startsWith("http://127.0.0.1:5173/");
}

function createWindow() {
  const window = new BrowserWindow({
    title: "Kendra",
    width: 1500,
    height: 980,
    minWidth: 980,
    minHeight: 720,
    backgroundColor: "#f5f1ea",
    show: false,
    icon: path.join(dashboardRoot, "public", "kendra-icon.png"),
    webPreferences: {
      preload: path.join(dashboardRoot, "electron", "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
    },
  });
  window.removeMenu();
  window.once("ready-to-show", () => window.show());

  // Renderer diagnostics. Without this, a React crash or a failed fetch inside
  // the UI is invisible unless devtools happen to be open.
  const levels = ["DEBUG", "INFO", "WARN", "ERROR"];
  window.webContents.on("console-message", (...args) => {
    // Electron 40 emits a single details object; older versions pass
    // positional args. Handle both so renderer errors are never invisible —
    // a night of blank renderer logs hid real front-end failures.
    let level, message, line, sourceId;
    if (args.length >= 4) [, level, message, line, sourceId] = args;
    else if (args[0] && typeof args[0] === "object") ({ level, message, lineNumber: line, sourceId } = args[0]);
    const name = (typeof level === "string" ? level.toUpperCase() : levels[level]) || "INFO";
    const where = sourceId ? ` (${path.basename(String(sourceId))}:${line})` : "";
    if (name === "ERROR") log.error("renderer", `${message}${where}`);
    else if (name === "WARN" || name === "WARNING") log.warn("renderer", `${message}${where}`);
    else log.info("renderer", `${message}${where}`);
  });
  window.webContents.on("render-process-gone", (_event, details) =>
    log.error("renderer", `process gone: ${details.reason} (exit ${details.exitCode})`),
  );
  window.webContents.on("preload-error", (_event, preloadPath, error) =>
    log.error("renderer", `preload failed ${preloadPath}`, error),
  );
  window.webContents.on("did-fail-load", (_event, code, description, url) =>
    log.error("renderer", `did-fail-load ${code} ${description} ${url}`),
  );
  window.webContents.on("unresponsive", () => log.warn("renderer", "window became unresponsive"));
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://github.com/NXTLupo/kendra")) shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith("file:") && url !== rendererUrl) event.preventDefault();
  });
  if (rendererUrl) window.loadURL(rendererUrl);
  else window.loadFile(path.join(dashboardRoot, "dist", "index.html"));
}

app.setName("Kendra");
if (singleInstance) app.whenReady().then(() => {
  log.info("main", `Kendra desktop starting: config=${config} python=${python} log=${logPath}`);
  // Show Kendra's face in the Dock while she is running, instead of the
  // generic Electron icon. The .app bundle icon only covers the launcher.
  if (process.platform === "darwin" && app.dock) {
    try {
      app.dock.setIcon(path.join(dashboardRoot, "public", "kendra-icon.png"));
    } catch (error) {
      log.warn("main", "Could not set the Dock icon", error);
    }
  }
  startBridge();
  session.defaultSession.setPermissionCheckHandler((_webContents, permission, origin) => {
    const local = origin.startsWith("file:") || origin.startsWith("http://127.0.0.1:5173");
    return local && ["media", "microphone", "camera"].includes(permission);
  });
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    callback(trustedRenderer(webContents.mainFrame) && ["media", "microphone", "camera"].includes(permission));
  });
  ipcMain.handle("kendra:request", (event, method, params) => {
    if (!trustedRenderer(event.senderFrame)) {
      log.error("ipc", `rejected ${method} from an untrusted renderer frame`);
      throw new Error("Untrusted desktop renderer");
    }
    return bridgeRequest(String(method), params && typeof params === "object" ? params : {});
  });
  createWindow();
  ensureRuntime()
    .then(() => log.info("runtime", "local models and Kendra services are ready"))
    .catch((error) => log.error("runtime", "Kendra runtime startup failed", error));
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
  app.on("second-instance", () => {
    const window = BrowserWindow.getAllWindows()[0];
    if (window) {
      if (window.isMinimized()) window.restore();
      window.focus();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
  else app.quit();
});

app.on("before-quit", () => {
  quitting = true;
  if (bridgeRestartTimer) clearTimeout(bridgeRestartTimer);
  if (bridge && !bridge.killed) bridge.kill("SIGTERM");
  for (const child of ownedModels) {
    if (!child.killed) child.kill("SIGTERM");
  }
  // Quitting the app means Kendra is OFF on this machine: her services are
  // independent processes (robot parity — the Pi has no app), so without
  // this she kept listening, watching, and TALKING after the window died,
  // which reads as haunted. Model servers stay resident (silent warm RAM);
  // every sense and her voice stop. Desktop only — never ships to the Pi.
  try {
    execSync(`pkill -f -- '-m kendra --config .* service '`, { stdio: "ignore" });
  } catch (_e) { /* nothing running is fine */ }
});
