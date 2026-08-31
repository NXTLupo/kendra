"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KendraBody } from "../src/KendraBody";

type ServiceState = { ok: boolean; error?: string; detail?: Record<string, unknown> };
type Turn = { id: number; user_text: string; kendra_text: string; created_at: string; metadata_json?: string };

function timingLine(turn: Turn): string | null {
  // App-side telemetry only: parses metadata the brain already stores.
  // This rendering never exists on the robot.
  try {
    const timings = JSON.parse(turn.metadata_json || "{}")?.timings;
    if (!timings?.total_s) return null;
    const parts = [`${timings.total_s}s total`];
    if (timings.sight_s) parts.push(`sight ${timings.sight_s}s`);
    if (timings.search_s) parts.push(`search ${timings.search_s}s`);
    return `${timings.kind || "turn"} · ${parts.join(" · ")}`;
  } catch {
    return null;
  }
}
type Memory = {
  id: number;
  kind: string;
  content: string;
  provenance: string;
  confidence: number;
  salience: number;
  created_at: string;
};
type Event = { id: number; event_type: string; payload: Record<string, unknown>; created_at: string };
type Snapshot = {
  interaction_mode?: string;
  generated_at: number;
  healthy_services: number;
  profile: { mode: string; body_driver: string; body_name: string; webots: boolean };
  services: Record<string, ServiceState>;
  models: { llm: boolean; vlm: boolean };
  body: {
    body_state?: string;
    reflex_lock?: boolean;
    reflex_fault?: boolean;
    front_cm?: number;
    battery?: { state?: string; voltage?: number };
    cliff?: Record<string, boolean>;
    notes?: string[];
    pose?: { x_m: number; y_m: number; heading_deg: number };
    last_motion?: { action?: string; direction?: string; steps?: number; degrees?: number; travelled_m?: number };
  };
  brain: {
    stats?: { active_memories?: number; counts?: Record<string, number>; bytes?: number };
    turns?: Turn[];
    events?: Event[];
    memories?: Memory[];
    goals?: Array<{ id: number; title: string; priority: number }>;
    questions?: Array<{ id: number; question: string }>;
  };
  git: {
    current_commit?: string;
    remote_commit?: string | null;
    upgrade_available?: boolean;
    working_tree_clean?: boolean;
    voice_install_enabled?: boolean;
    error?: string;
  };
  latest_photo?: { name: string; modified: number } | null;
};

async function request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  if (!window.kendra) throw new Error("Open the native Kendra desktop app to connect locally");
  return window.kendra.request<T>(method, params);
}

function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const step = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += step) {
    binary += String.fromCharCode(...bytes.subarray(index, index + step));
  }
  return btoa(binary);
}

function shortTime(value?: string) {
  if (!value) return "now";
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function bytes(value = 0) {
  if (value < 1024 * 1024) return `${Math.max(0, Math.round(value / 1024))} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export default function Home() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [online, setOnline] = useState(false);
  const [activeView, setActiveView] = useState<"presence" | "memory" | "activity" | "system">("presence");
  const [chatText, setChatText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState("Waiting for Kendra’s local services…");
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memoryResults, setMemoryResults] = useState<Memory[]>([]);
  const [wifiHost, setWifiHost] = useState("kendra.local");
  const [wifiUser, setWifiUser] = useState("kendra");
  const [cameraResult, setCameraResult] = useState<{ description?: string; path?: string } | null>(null);
  const [photoDataUrl, setPhotoDataUrl] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const value = await request<Snapshot>("snapshot");
      setSnapshot(value);
      setOnline(true);
      setNotice(`${value.healthy_services}/10 local services are responding`);
    } catch {
      setOnline(false);
      setNotice("Kendra’s native bridge is offline — start Virtual Kendra to reconnect");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(refresh, 0);
    // Voice-first mode: the app is a window into her mind, not a control
    // surface — calmer polling leaves the CPU to her senses and speech.
    const timer = window.setInterval(refresh, 3000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const latestPhotoName = snapshot?.latest_photo?.name;
  useEffect(() => {
    if (!latestPhotoName) return;
    let active = true;
    request<{ data_url: string }>("photo", { name: latestPhotoName })
      .then((value) => { if (active) setPhotoDataUrl(value.data_url); })
      .catch(() => { if (active) setPhotoDataUrl(null); });
    return () => { active = false; };
  }, [latestPhotoName]);

  const turns = useMemo(() => [...(snapshot?.brain.turns || [])].reverse(), [snapshot]);
  const serviceEntries = Object.entries(snapshot?.services || {});
  const agentReady = Boolean(snapshot?.services.agent?.ok && snapshot?.models.llm);
  const voiceReady = Boolean(agentReady && snapshot?.services.voice?.ok);
  const visionReady = Boolean(snapshot?.services.vision?.ok);
  const runAction = async <T,>(label: string, action: () => Promise<T>, success: (value: T) => string) => {
    setBusy(label);
    try {
      const value = await action();
      setNotice(success(value));
      await refresh();
      return value;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The local action failed");
      return null;
    } finally {
      setBusy(null);
    }
  };

  const submitChat = async (event: FormEvent) => {
    event.preventDefault();
    const text = chatText.trim();
    if (!text) return;
    setChatText("");
    await runAction(
      "chat",
      () => request<{ text: string }>("chat", { text }),
      (value) => `Kendra: ${value.text}`,
    );
  };


  // Hands-free turn. Kendra opens her own microphone and her energy VAD ends
  // the capture when you stop talking, so there is nothing to click to finish.
  // This is the same code path the robot body runs after the wake phrase --
  // there will be no Stop button on the Pi.
  const listen = async () => {
    if (recording) return;
    setRecording(true);
    setNotice("Listening… just stop talking when you're done");
    try {
      await runAction(
        "voice",
        () => request<{ heard: string; response: string }>("listen"),
        (value) =>
          value.heard
            ? `Heard “${value.heard}” — ${value.response}`
            : "I didn't hear speech; please try again",
      );
    } finally {
      setRecording(false);
    }
  };


  // Kendra's desktop eyes. macOS grants the webcam to this app, never to the
  // headless Python service, so the renderer holds the camera and streams a
  // frame to the vision service every few seconds. On the robot the Pi camera
  // feeds the identical service directly. Sensing is disclosed in her charter.
  const eyeStreamRef = useRef<MediaStream | null>(null);
  const pushFrameRef = useRef<(() => Promise<void>) | null>(null);
  useEffect(() => {
    // Gate only on the app being online: gating on visionReady tore the
    // stream down whenever the vision service restarted, and it never came
    // back — her eyes went dark for good until an app relaunch. Frames sent
    // to a briefly-dead service fail harmlessly and resume on their own.
    if (!online) return;
    let cancelled = false;
    const video = document.createElement("video");
    video.muted = true;
    const canvas = document.createElement("canvas");
    const pushFrame = async () => {
      if (cancelled || !eyeStreamRef.current || video.readyState < 2) return;
      // 640px was too coarse for her eyes: Moondream could not resolve hands
      // or small objects and invented them ("a cigarette", "a wooden box")
      // when Jonathan's hands were not even in frame.
      canvas.width = 1280;
      canvas.height = Math.round((video.videoHeight / video.videoWidth) * 1280) || 720;
      canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
      const jpeg = canvas.toDataURL("image/jpeg", 0.85).split(",", 2)[1];
      if (jpeg) await request("vision_frame", { image: jpeg }).catch(() => undefined);
    };
    pushFrameRef.current = pushFrame;
    let timer: number | null = null;
    let watchdog: number | null = null;
    navigator.mediaDevices
      .getUserMedia({ video: { width: 1280, height: 720 } })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        eyeStreamRef.current = stream;
        video.srcObject = stream;
        void video.play();
        timer = window.setInterval(() => void pushFrame(), 2000);
        window.setTimeout(() => void pushFrame(), 1500);
        // Eye watchdog: re-acquire the camera if the track dies (bridge
        // restart, device grab, display sleep). Her eyes once stayed dark
        // for ten minutes after a model-server swap killed the bridge.
        watchdog = window.setInterval(() => {
          if (cancelled) return;
          const track = eyeStreamRef.current?.getVideoTracks?.()[0];
          if (track && track.readyState === "live" && video.readyState >= 2) return;
          eyeStreamRef.current?.getTracks().forEach((old) => old.stop());
          eyeStreamRef.current = null;
          navigator.mediaDevices
            .getUserMedia({ video: { width: 1280, height: 720 } })
            .then((fresh) => {
              if (cancelled) {
                fresh.getTracks().forEach((tr) => tr.stop());
                return;
              }
              eyeStreamRef.current = fresh;
              video.srcObject = fresh;
              void video.play();
            })
            .catch(() => undefined);
        }, 20000);
      })
      .catch(() => setNotice("Kendra's eyes need camera permission — approve it to let her see"));
    return () => {
      cancelled = true;
      if (timer != null) window.clearInterval(timer);
      if (watchdog != null) window.clearInterval(watchdog);
      eyeStreamRef.current?.getTracks().forEach((track) => track.stop());
      eyeStreamRef.current = null;
    };
    // Depend on `online` ONLY: visionReady flapping (service restart) used
    // to tear the camera down mid-session.
  }, [online]);

  const observe = async () => {
    // A fresh frame right now, so the button never races the 5s eye-stream.
    await pushFrameRef.current?.().catch(() => undefined);
    const value = await runAction(
      "observe",
      () =>
        request<{ description?: string; path?: string }>("observe", {
          semantic: false,
          question: "Capture what is in front of the iMac camera now.",
        }),
      (result) => result.description || "Kendra captured a new local camera view; ask what she sees for semantic analysis",
    );
    if (value) setCameraResult(value);
  };

  const bodyCommand = (command: string, params: Record<string, unknown> = {}) =>
    runAction(
      `body-${command}`,
      () => request<Record<string, unknown>>("body", { command, params }),
      () => `${command[0].toUpperCase()}${command.slice(1)} sent to the ${snapshot?.profile.webots ? "Webots body" : "simulation body"}`,
    );

  const searchMemories = async (event: FormEvent) => {
    event.preventDefault();
    const query = memoryQuery.trim();
    if (!query) return;
    const value = await runAction(
      "memory-search",
      () => request<{ memories: Memory[] }>("memories", { query, limit: 30 }),
      (result) => `${result.memories.length} matching memories found locally`,
    );
    if (value) setMemoryResults(value.memories);
  };

  const importBrain = async (file: File) => {
    setBusy("brain-import");
    try {
      const value = await request<{ imported: number; duplicates: number }>("memory_import", {
        filename: file.name,
        data: bufferToBase64(await file.arrayBuffer()),
      });
      setNotice(`Transferred ${value.imported} memories; ${value.duplicates} duplicates were safely skipped`);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Memory transfer failed");
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const wifiSync = () =>
    runAction(
      "wifi-sync",
      () =>
        request<{ imported: number; duplicates: number }>("memory_sync", { host: wifiHost, user: wifiUser }),
      (value) => `Encrypted sync imported ${value.imported} memories and skipped ${value.duplicates} duplicates`,
    );

  const checkUpgrade = () =>
    runAction(
      "update-check",
      () => request<Snapshot["git"]>("update_check"),
      (value) =>
        value.upgrade_available
          ? "A newer Git intelligence revision is available; only a signed release can be installed"
          : "Kendra’s checked Git channel is current",
    );

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>
          <div><p className="eyebrow">Local companion runtime</p><h1>Kendra</h1></div>
        </div>
        <nav className="nav-tabs" aria-label="Dashboard views">
          {(["presence", "memory", "activity", "system"] as const).map((view) => (
            <button key={view} className={activeView === view ? "active" : ""} onClick={() => setActiveView(view)}>
              {view}
            </button>
          ))}
        </nav>
        <div className={`connection-pill ${online ? "online" : "offline"}`}>
          <span className="status-dot" />
          {online ? "Together on this iMac" : "Waiting locally"}
        </div>
      </header>

      <section className="notice-bar" aria-live="polite">
        <span>{busy ? "Kendra is working locally" : notice}</span>
        <span className="profile-chip">{snapshot?.profile.webots ? "3D body · config/webots.yaml" : "desktop simulation"}</span>
        {snapshot?.body?.pose ? (
          <span className="profile-chip" title="Where her simulated body is standing right now">
            {`🕷 ${snapshot.body.pose.x_m.toFixed(2)}m, ${snapshot.body.pose.y_m.toFixed(2)}m · facing ${Math.round(snapshot.body.pose.heading_deg)}°`}
            {snapshot.body.last_motion?.action === "walk" && snapshot.body.last_motion?.travelled_m
              ? ` · last walk ${snapshot.body.last_motion.travelled_m}m ${snapshot.body.last_motion.direction ?? ""}`
              : snapshot.body.last_motion?.action === "turn"
                ? ` · last turn ${snapshot.body.last_motion.degrees}°`
                : ""}
          </span>
        ) : null}
      </section>

      {activeView === "presence" && (
        <div className="dashboard-grid presence-grid">
          <aside className="left-rail">
            <section className="soft-card service-card">
              <div className="card-heading"><div><p className="eyebrow">Heartbeat</p><h2>Her local systems</h2></div><b>{snapshot?.healthy_services || 0}/10</b></div>
              <div className="service-list">
                {serviceEntries.map(([name, state]) => (
                  <div className="service-row" key={name}><span className={`mini-dot ${state.ok ? "good" : "quiet"}`} /><span>{name}</span><small>{state.ok ? "present" : "resting"}</small></div>
                ))}
                {!serviceEntries.length && <p className="empty-copy">Start Virtual Kendra to see each service arrive.</p>}
              </div>
            </section>

            <section className="soft-card safety-card">
              <div className="card-heading"><div><p className="eyebrow">Independent reflex</p><h2>Body & safety</h2></div><span className={`safety-state ${snapshot?.body.reflex_lock ? "locked" : "clear"}`}>{snapshot?.body.reflex_lock ? "locked" : "clear"}</span></div>
              <div className="sensor-reading"><span>Front space</span><strong>{snapshot?.body.front_cm != null ? `${snapshot.body.front_cm.toFixed(0)} cm` : "—"}</strong></div>
              <div className="sensor-reading"><span>Body state</span><strong>{snapshot?.body.body_state || "offline"}</strong></div>
              <div className="sensor-reading"><span>Battery model</span><strong>{snapshot?.body.battery?.voltage ? `${snapshot.body.battery.voltage.toFixed(1)} V` : "—"}</strong></div>
              <div className="cliff-array" aria-label="Cliff sensor state">
                {(["fl", "fr", "rl", "rr"] as const).map((sensor) => <span key={sensor} className={snapshot?.body.cliff?.[sensor] ? "danger" : ""}>{sensor.toUpperCase()}</span>)}
              </div>
            </section>
          </aside>

          <section className="center-stage">
            <div className={`presence-orb ${busy ? "thinking" : ""} ${recording ? "listening" : ""}`} aria-label={recording ? "Kendra is listening" : busy ? "Kendra is working" : "Kendra is ready"}>
              <div className="halo halo-one" /><div className="halo halo-two" />
              <div className="kendra-portrait">
                <KendraBody
                  pose={snapshot?.body?.pose}
                  busy={busy}
                  latestReply={turns.length ? (turns[turns.length - 1]?.kendra_text ?? null) : null}
                  listening={Boolean(voiceReady) && !busy}
                  // A fault, never a routine servo rest: `reflex_lock` is also
                  // set while her legs take their normal breather after a walk,
                  // and drawing that as alarm turned her red every time she moved.
                  startled={Boolean(
                    snapshot?.body?.reflex_fault ||
                    Object.values(snapshot?.body?.cliff || {}).some(Boolean),
                  )}
                />
              </div>
              <span className="presence-shadow" />
            </div>
            <div className="presence-copy">
              <p className="eyebrow">{snapshot?.profile.webots ? "Embodied in Webots" : "Virtual presence"}</p>
              <h2>{busy ? "I’m with you…" : "Ready when you are."}</h2>
              <p>Voice, vision, memory, and motion stay on this computer while you shape who Kendra becomes.</p>
            </div>
            <div className="primary-actions">
              <button className={`voice-button ${recording ? "recording" : ""}`} onClick={listen} disabled={!voiceReady || (!!busy && busy !== "voice")}><span className="mic-shape" />{recording ? "Listening — just stop talking" : busy === "voice" ? "Thinking & speaking…" : voiceReady ? "Talk with Kendra" : "Voice is starting…"}</button>
              <button className="soft-button" onClick={observe} disabled={!visionReady || !!busy}><span className="camera-shape" />{visionReady ? "Use my webcam" : "Eyes are starting…"}</button>
            </div>
            <div className="motion-pad">
              <button onClick={() => bodyCommand("look", { pan: -18, tilt: 0 })}>Look left</button>
              <button onClick={() => bodyCommand("walk", { direction: "forward", steps: 1, speed: 0.25 })}>Step forward</button>
              <button onClick={() => bodyCommand("look", { pan: 18, tilt: 0 })}>Look right</button>
              <button onClick={() => bodyCommand("pose", { name: "rest" })}>Rest</button>
              <button className="stop" onClick={() => bodyCommand("stop", { reason: "dashboard stop" })}>Stop</button>
              <button onClick={() => bodyCommand("pose", { name: "alert" })}>Alert pose</button>
            </div>
          </section>

          <aside className="right-rail">
            <section className="soft-card conversation-card">
              <div className="card-heading"><div><p className="eyebrow">What we’re saying</p><h2>Live conversation</h2></div><span className="private-label">private</span></div>
              <div className="conversation-stream">
                {turns.slice(-6).map((turn) => (
                  <div className="conversation-pair" key={turn.id}><p className="you"><b>You</b>{turn.user_text}</p><p className="kendra"><b>Kendra</b>{turn.kendra_text}</p><time>{shortTime(turn.created_at)}{timingLine(turn) ? ` · ⏱ ${timingLine(turn)}` : ""}</time></div>
                ))}
                {!turns.length && <p className="empty-copy">Your local conversations will gather here.</p>}
              </div>
              {snapshot?.interaction_mode === "voice_first" ? (
                <p className="empty-copy">Voice-first mode — say “Kendra” and talk to her. The transcript gathers here.</p>
              ) : (
                <form className="chat-composer" onSubmit={submitChat}>
                  <input aria-label="Message Kendra" placeholder="Ask Kendra anything…" value={chatText} onChange={(event) => setChatText(event.target.value)} />
                  <button aria-label="Send message" disabled={!chatText.trim() || busy === "chat"}>↑</button>
                </form>
              )}
            </section>

            <section className="soft-card eyes-card">
              <div className="card-heading"><div><p className="eyebrow">Her eyes for now</p><h2>iMac webcam</h2></div><button className="text-button" onClick={observe}>refresh</button></div>
              <div className="camera-window">
                {photoDataUrl ? <img src={photoDataUrl} alt="Latest local frame seen by Kendra" /> : <div className="camera-placeholder"><span className="camera-shape large" /><p>No frame captured yet</p></div>}
                <span className="local-badge">local only</span>
              </div>
              <p className="vision-caption">{cameraResult?.description || "Ask Kendra to look; the frame and semantic description never need to leave this iMac."}</p>
            </section>
          </aside>
        </div>
      )}

      {activeView === "memory" && (
        <div className="page-panel memory-page">
          <section className="memory-hero">
            <div><p className="eyebrow">Kendra’s second brain</p><h2>Bring every shared moment home.</h2><p>Search what she remembers, make an encrypted Wi-Fi pull, or import the JSONL archive when her body is plugged into your iMac.</p></div>
            <div className="memory-total"><strong>{snapshot?.brain.stats?.active_memories || 0}</strong><span>active memories</span><small>{bytes(snapshot?.brain.stats?.bytes)}</small></div>
          </section>
          <div className="memory-layout">
            <section className="soft-card memory-browser">
              <form className="memory-search" onSubmit={searchMemories}><input placeholder="Search people, preferences, places, moments…" value={memoryQuery} onChange={(event) => setMemoryQuery(event.target.value)} /><button>Search memory</button></form>
              <div className="memory-list">
                {(memoryResults.length ? memoryResults : snapshot?.brain.memories || []).map((memory) => (
                  <article key={memory.id}><div><span className={`provenance ${memory.provenance}`}>{memory.provenance.replace("_", " ")}</span><span>{memory.kind}</span></div><p>{memory.content}</p><footer><span>{Math.round(memory.confidence * 100)}% confidence</span><time>{shortTime(memory.created_at)}</time></footer></article>
                ))}
              </div>
            </section>
            <aside className="transfer-stack">
              <section className="soft-card transfer-card"><p className="eyebrow">Cable or removable drive</p><h3>Import from her body</h3><p>Choose a Kendra Brain JSONL export. Existing memories are deduplicated and biometric data is never included.</p><input ref={fileInput} type="file" accept=".jsonl,application/x-ndjson" hidden onChange={(event) => event.target.files?.[0] && importBrain(event.target.files[0])} /><button onClick={() => fileInput.current?.click()} disabled={!!busy}>Choose brain export</button></section>
              <section className="soft-card transfer-card"><p className="eyebrow">Encrypted local Wi-Fi</p><h3>Ask her second brain</h3><p>Uses your pre-authorized SSH key and strict host verification — never a hosted sync service.</p><label>Robot hostname<input value={wifiHost} onChange={(event) => setWifiHost(event.target.value)} /></label><label>SSH user<input value={wifiUser} onChange={(event) => setWifiUser(event.target.value)} /></label><button onClick={wifiSync} disabled={!!busy}>Retrieve memories now</button></section>
              <section className="soft-card transfer-card compact"><p className="eyebrow">Local resilience</p><h3>Make a brain backup</h3><button onClick={() => runAction("backup", () => request<{ path: string }>("memory_backup"), (value) => `Backup saved locally: ${value.path}`)}>Create safe snapshot</button></section>
            </aside>
          </div>
        </div>
      )}

      {activeView === "activity" && (
        <div className="page-panel activity-page">
          <div className="section-intro"><p className="eyebrow">Local observability</p><h2>A quiet record of what Kendra does.</h2><p>Conversations, memory changes, tools, and safety state are visible here without sending telemetry away.</p></div>
          <div className="activity-columns">
            <section className="soft-card"><div className="card-heading"><h3>Recent conversations</h3><span>{turns.length}</span></div>{[...turns].reverse().map((turn) => <article className="activity-row" key={turn.id}><span className="activity-icon speech">“</span><div><b>You: {turn.user_text}</b><p>Kendra: {turn.kendra_text}</p></div><time>{shortTime(turn.created_at)}</time></article>)}</section>
            <section className="soft-card"><div className="card-heading"><h3>Cognitive events</h3><span>{snapshot?.brain.events?.length || 0}</span></div>{(snapshot?.brain.events || []).map((event) => <article className="activity-row" key={event.id}><span className="activity-icon memory">✦</span><div><b>{event.event_type.replaceAll("_", " ")}</b><p>{Object.entries(event.payload).map(([key, value]) => `${key}: ${String(value)}`).join(" · ")}</p></div><time>{shortTime(event.created_at)}</time></article>)}</section>
          </div>
        </div>
      )}

      {activeView === "system" && (
        <div className="page-panel system-page">
          <div className="section-intro"><p className="eyebrow">Sovereign runtime</p><h2>Local intelligence, visible and deliberate.</h2><p>Improve Kendra on this iMac, commit the release, and let her check the fixed repository. Raw Git code is never activated on her body without a signed release.</p></div>
          <div className="system-grid">
            <section className="soft-card release-card"><div className="release-orb">Git</div><div><p className="eyebrow">Intelligence channel</p><h3>{snapshot?.git.upgrade_available ? "A revision is waiting" : "This checkout is current"}</h3><p>{snapshot?.git.current_commit ? `Local ${snapshot.git.current_commit.slice(0, 9)}` : "First release is being prepared"}</p></div><button onClick={checkUpgrade} disabled={!!busy}>Check GitHub now</button></section>
            <section className="soft-card policy-card"><p className="eyebrow">Voice upgrade policy</p><h3>Signed, confirmed, and fail-closed</h3><ol><li>You say, “Check for an intelligence upgrade.”</li><li>Kendra checks only the pinned repository.</li><li>She installs only a signed A/B release after the exact confirmation phrase.</li><li>Unsigned code, dirty trees, or missing keys are refused.</li></ol><div className={`policy-status ${snapshot?.git.voice_install_enabled ? "enabled" : "locked"}`}><span />{snapshot?.git.voice_install_enabled ? "Signed voice install enabled" : "Installation locked until your release key is configured"}</div></section>
            <section className="soft-card models-card"><p className="eyebrow">Local models</p><h3>Inference presence</h3><div className="model-row"><span className={`mini-dot ${snapshot?.models.llm ? "good" : "quiet"}`} /><div><b>Qwen Pi-parity brain</b><small>0.6B Q8 · llama.cpp · local</small></div></div><div className="model-row"><span className={`mini-dot ${snapshot?.models.vlm ? "good" : "quiet"}`} /><div><b>Semantic vision</b><small>multimodal llama.cpp · local</small></div></div><div className="model-row"><span className={`mini-dot ${snapshot?.services.voice?.ok ? "good" : "quiet"}`} /><div><b>Local voice loop</b><small>Vosk · Moonshine · Piper</small></div></div></section>
            <section className="soft-card webots-card"><p className="eyebrow">3D embodiment</p><h3>{snapshot?.profile.webots ? "Webots body selected" : "Desktop body selected"}</h3><p>Use <code>config/webots.yaml</code> whenever Kendra should turn, walk, look, and pose in the 3D Virtual Kendra world.</p><div className="webots-figure" aria-hidden="true"><span /><span /><span /><span /><span /><span /><i /></div></section>
          </div>
        </div>
      )}
    </main>
  );
}
