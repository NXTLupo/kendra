/**
 * Kendra, embodied on screen.
 *
 * Her portrait used to be a still PNG. This mounts her actual Tripo model
 * and drives it from the same snapshot the dashboard already polls, so the
 * body on screen does what the real Kendra is doing: walking when her pose
 * changes, leaning in when her eyes work, swaying when she sings, and
 * blinking on her own.
 *
 * Every signal already exists — no new plumbing into her services:
 *   - body.pose changing between polls -> she is really moving
 *   - busy === "sight"                 -> her eyes are working
 *   - a reply starting "(sings" / "(hums" / "(plays" -> a performance, since
 *     her expression engine writes exactly those markers into the transcript
 */

import { useEffect, useRef } from "react";

import { KendraStage, type KendraMood } from "./kendraStage";

type BodyPose = { x_m: number; y_m: number; heading_deg: number } | undefined;

export type KendraBodyProps = {
  pose: BodyPose;
  busy: string | null;
  latestReply: string | null;
  listening: boolean;
};

const PERFORMANCE = /^\((sings|hums|plays|dances)/i;

export function KendraBody({ pose, busy, latestReply, listening }: KendraBodyProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stageRef = useRef<KendraStage | null>(null);
  const lastPose = useRef<BodyPose>(undefined);
  const movingUntil = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const stage = new KendraStage(canvas);
    stageRef.current = stage;
    let cancelled = false;
    stage.load().catch((error: unknown) => {
      // A missing model must never break the dashboard.
      console.warn("Kendra's 3D body could not load", error);
      if (!cancelled) canvas.classList.add("kendra-body-failed");
    });
    const onResize = () => stage.resize();
    window.addEventListener("resize", onResize);
    return () => {
      cancelled = true;
      window.removeEventListener("resize", onResize);
      stage.dispose();
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
      }
    }
    lastPose.current = pose;

    let mood: KendraMood = "idle";
    if (latestReply && PERFORMANCE.test(latestReply.trim())) {
      mood = /plays|dances/i.test(latestReply) ? "delighted" : "singing";
    } else if (performance.now() < movingUntil.current) {
      mood = "walking";
    } else if (busy === "sight") {
      mood = "curious";
    } else if (busy) {
      mood = "thinking";
    } else if (listening) {
      mood = "listening";
    }
    stage.setMood(mood);
  }, [pose, busy, latestReply, listening]);

  return (
    <div className="kendra-body">
      <canvas ref={canvasRef} aria-label="Kendra, animated" />
    </div>
  );
}
