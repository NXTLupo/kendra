/**
 * Kendra, embodied on screen.
 *
 * She is drawn, not modelled — see kendraSprite.ts for why. This mounts the
 * sprite and drives it from the same snapshot the dashboard already polls,
 * so the
 * body on screen does what the real Kendra is doing: walking when her pose
 * changes, leaning in when her eyes work, swaying when she sings, and
 * blinking on her own.
 *
 * Every signal already exists — no new plumbing into her services:
 *   - body.pose changing between polls -> she is really moving, and how far
 *     it moved separates a walk from a run
 *   - busy === "sight"                 -> her eyes are working
 *   - a NEW reply appearing            -> she is speaking it aloud
 *   - that reply opening with a greeting -> her greeting ritual is running
 *   - a reply starting "(sings" / "(hums" / "(plays" -> a performance, since
 *     her expression engine writes exactly those markers into the transcript
 *   - reflex_lock or any cliff sensor  -> something startled her
 *
 * SPEECH AND THINKING ARE NOT INFERRED. They arrive as live events on the
 * face bus (`window.kendra.onEvent`), published by her voice service as the
 * first audio sample reaches the speaker and as the thinking tones start.
 *
 * They used to be inferred, and that was the whole bug. A reply is written to
 * the brain only AFTER she finishes saying it, and the dashboard polls that
 * transcript every three seconds — so her mouth began moving once the audio
 * was already over, ran on a 13-characters-per-second guess, and two replies
 * inside one poll window arrived as a single rushed burst. Polling still
 * drives pose and sentiment, which are genuinely snapshot-shaped. Timing is
 * never polled again.
 */

import { useEffect, useRef, useState } from "react";

import { KendraSprite, type KendraMood } from "./kendraSprite";

type BodyPose = { x_m: number; y_m: number; heading_deg: number } | undefined;

export type KendraBodyProps = {
  pose: BodyPose;
  busy: string | null;
  latestReply: string | null;
  listening: boolean;
  /** a reflex fired — an obstacle, or a cliff sensor tripping */
  startled?: boolean;
};

const PERFORMANCE = /^\((sings|hums|plays|dances)/i;

// Her greeting ritual: approach, introduce, ask the name. The opening words
// are what reach the transcript, so that is what is matched.
const GREETING = /^\s*(?:\(\w+\)\s*)?(hi\b|hey\b|hello\b|good (?:morning|afternoon|evening)\b|nice to meet you\b|i'?m kendra\b|my name is kendra\b)/i;

/**
 * How warm or bleak a line is, from -1 to +1.
 *
 * A lexicon, not a model: this runs on every reply and must never cost a
 * turn. It feeds `sprite.feel()`, which drifts her colour slowly, so being
 * approximately right is enough — the smoothing absorbs the noise. The same
 * value is what `kendra/leds/` should read on the Pi.
 */
const WARM = /\b(love|lovely|glad|happy|wonderful|beautiful|thank you|thanks|great|delight\w*|enjoy\w*|excited|proud|kind|warm|funny|laugh\w*|yes|good|nice|brilliant|perfect|amazing)\b/gi;
const BLEAK = /\b(sorry|sad|afraid|worried|hurt|tired|lonely|angry|upset|wrong|broken|fail\w*|can'?t|don'?t|never|bad|awful|hate|difficult|problem|error)\b/gi;

function sentimentOf(text: string): number {
  const line = text || "";
  const warm = (line.match(WARM) || []).length;
  const bleak = (line.match(BLEAK) || []).length;
  if (!warm && !bleak) return 0;
  // Normalised by total hits, so a long warm paragraph and a short warm
  // sentence read the same. Volume is carried by how often she feels it.
  return (warm - bleak) / (warm + bleak);
}

// The face-event shape lives in src/kendra.d.ts alongside the rest of the
// preload surface, so there is exactly one description of the bridge.

export function KendraBody({ pose, busy, latestReply, listening, startled }: KendraBodyProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stageRef = useRef<KendraSprite | null>(null);
  const lastPose = useRef<BodyPose>(undefined);
  const movingUntil = useRef(0);
  const running = useRef(false);
  const lastReply = useRef<string | null>(null);
  // Live state from the face bus. Refs, not React state: these change many
  // times per turn and must never queue a re-render to reach the canvas.
  const speaking = useRef(false);
  const thinkingMode = useRef<string | null>(null);
  const micOpen = useRef(false);
  // A song, hum or tune rather than ordinary speech.
  const performing = useRef(false);
  // Her legs are stepping right now, from a real body command.
  const walking = useRef(false);
  // Every microphone returned pure silence — she cannot hear at all.
  const deaf = useRef(false);
  // Re-run the mood selector when a live event lands, not only when a poll
  // completes. One counter is enough to nudge the effect below.
  const [pulse, setPulse] = useState(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const stage = new KendraSprite(canvas);
    stageRef.current = stage;
    // Debug handle. Her moods come from her services, so there is no other
    // way to put her into one on demand and check she looks right:
    //   __kendra.setMood("delighted")
    (window as unknown as { __kendra?: KendraSprite }).__kendra = stage;
    // Nothing to load. She is drawn, so she is on screen this frame — no
    // GLB fetch, no decode, no "could not load" path to handle.
    stage.start();
    const onResize = () => stage.resize();
    window.addEventListener("resize", onResize);

    // Her voice, driving her face directly. `speech_start` fires as the first
    // audio sample reaches the speaker and carries the utterance's real
    // length, so the mouth opens in the same frame the sound does.
    const unsubscribe = window.kendra?.onEvent?.((message) => {
      switch (message.event) {
        case "speech_start": {
          deaf.current = false;
          const text = String(message.data?.text || "");
          const seconds = Number(message.data?.seconds) || 0;
          // A hum or a tune arrives with no text at all. It is still her
          // voice, and her mouth still has to move for it.
          const kind = (String(message.data?.kind || "speech")) as
            "speech" | "song" | "hum" | "tune";
          if (text || seconds > 0) stage.speak(text, seconds, kind);
          performing.current = kind !== "speech";
          speaking.current = true;
          thinkingMode.current = null;
          micOpen.current = false;
          stage.stopThinking();
          break;
        }
        case "speech_end":
          stage.endSpeech();
          speaking.current = stage.speaking;
          if (!speaking.current) performing.current = false;
          break;
        case "thinking":
          thinkingMode.current = String(message.data?.mode || "think");
          micOpen.current = false;
          stage.think(thinkingMode.current as "think" | "research" | "sight");
          break;
        case "listening":
          deaf.current = false;
          micOpen.current = true;
          thinkingMode.current = null;
          stage.stopThinking();
          break;
        // Something caught her eye without being asked. Distinct from a
        // "take a look" command, which engages the camera focus instead.
        // She cannot hear anything at all. Distinct from silence: an
        // unauthorized microphone on macOS returns zeros forever, and a
        // companion who cannot hear looks exactly like one that has crashed.
        case "deaf":
          deaf.current = true;
          micOpen.current = false;
          thinkingMode.current = null;
          stage.stopThinking();
          break;
        case "curious":
          thinkingMode.current = "curious";
          micOpen.current = false;
          stage.stopThinking();
          break;
        case "idle":
          thinkingMode.current = null;
          micOpen.current = false;
          stage.stopThinking();
          break;
        // Her legs, as her body service commands them. `cycle_seconds` is the
        // vendor's real 0.4 s tripod period, so what you see stepping is the
        // rate the servos will step at.
        case "gait": {
          const action = String(message.data?.action || "walk") as "walk" | "turn";
          const direction = String(message.data?.direction || "forward") as
            "forward" | "backward" | "left" | "right";
          stage.move(
            action,
            direction,
            Number(message.data?.cycles) || 1,
            Number(message.data?.cycle_seconds) || 0.4,
            Number(message.data?.speed) || 0.35,
          );
          walking.current = true;
          break;
        }
        case "gait_end":
          stage.stopMoving();
          walking.current = false;
          break;
        default:
          return;
      }
      setPulse((value) => value + 1);
    });

    return () => {
      window.removeEventListener("resize", onResize);
      unsubscribe?.();
      stage.stop();
      stageRef.current = null;
    };
  }, []);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    // Movement is inferred from her pose really changing, so she walks on
    // screen only when her body actually moved.
    const previous = lastPose.current;
    if (pose && previous) {
      const distance = Math.hypot(pose.x_m - previous.x_m, pose.y_m - previous.y_m);
      const turned = Math.abs(pose.heading_deg - previous.heading_deg);
      if (distance > 0.005 || turned > 1.5) {
        movingUntil.current = performance.now() + 2500;
        // Covering real ground between two polls is a run, not a stroll.
        running.current = distance > 0.08;
      }
    }
    lastPose.current = pose;

    // A new reply drifts her colour. Note what this NO LONGER does: it does
    // not start her mouth. The transcript is written after she has finished
    // speaking, so using it for timing was the desync -- speech now begins
    // on the `speech_start` event instead. Sentiment is genuinely
    // snapshot-shaped and stays here.
    if (latestReply && latestReply !== lastReply.current) {
      lastReply.current = latestReply;
      stage.feel(sentimentOf(latestReply));
    }

    let mood: KendraMood = "idle";
    const now = performance.now();
    // Deafness outranks idling. Sitting in `idle` while unable to hear a word
    // is what produced "she's dead and not reacting at all" -- she looked
    // exactly as she does when simply waiting.
    if (deaf.current && !speaking.current && !stage.speaking) {
      stage.setMood("startled");
      stage.setDeaf(true);
      return;
    }
    stage.setDeaf(false);
    const isSpeaking = speaking.current || stage.speaking;
    if (performing.current || (isSpeaking && latestReply && PERFORMANCE.test(latestReply.trim()))) {
      mood = /plays|dances/i.test(latestReply ?? "") ? "delighted" : "singing";
    } else if (startled) {
      // A reflex outranks everything: she reacts before she thinks.
      mood = "startled";
    } else if (isSpeaking) {
      mood = GREETING.test(latestReply ?? "") ? "greeting" : "talking";
    } else if (thinkingMode.current) {
      // Live, from the same signal that started her thinking tones. Her voice
      // service already separates these three, so each gets its own read
      // rather than all of them collapsing into "busy".
      // her voice service's own vocabulary: think | research | sight
      mood =
        thinkingMode.current === "sight" ? "looking"
        : thinkingMode.current === "research" ? "researching"
        : thinkingMode.current === "curious" ? "curious"
        : "thinking";
    } else if (micOpen.current) {
      mood = "listening";
    } else if (walking.current || stage.moving) {
      // From the body service itself, not inferred from two poll samples.
      mood = running.current ? "running" : "walking";
    } else if (now < movingUntil.current) {
      mood = running.current ? "running" : "walking";
    } else if (busy === "sight") {
      mood = "looking";
    } else if (busy === "research") {
      mood = "researching";
    } else if (busy) {
      mood = "thinking";
    } else if (listening) {
      mood = "listening";
    }
    stage.setMood(mood);
    // Publish her emotional colour for anything else that wants it. On the
    // Pi this is what the LED ring reads, so the screen and the robot show
    // the same feeling from one signal rather than two implementations.
    const feeling = stage.emotion();
    window.dispatchEvent(new CustomEvent("kendra:emotion", { detail: feeling }));
    (window as unknown as { __kendraEmotion?: unknown }).__kendraEmotion = feeling;
  }, [pose, busy, latestReply, listening, startled, pulse]);

  return (
    <div className="kendra-body">
      <canvas ref={canvasRef} aria-label="Kendra, animated" />
    </div>
  );
}
