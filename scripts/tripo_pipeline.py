#!/usr/bin/env python3
"""Generate, rig and animate Kendra's 3D model through Tripo.

Runs the whole chain in one command so the credit budget is spent
deliberately rather than by trial and error:

    upload cutout -> image_to_model -> rig -> animations -> download GLBs

Budget discipline (Jonathan has ~200 free generations for the life of the
project, and none of this touches the robot body — it is purely Virtual
Kendra's on-screen puppet):

- ONE model generation. No prompt iteration; the cutout is already clean.
- ONE rig.
- A small animation set chosen so BLENDING them covers her whole
  expressive range, rather than generating a clip per emotion.

Every step is checkpointed to data/tripo/state.json, so a rerun resumes
instead of re-spending. Nothing is regenerated if its artefact exists.

Usage:
  .venv/bin/python scripts/tripo_pipeline.py            # run/resume
  .venv/bin/python scripts/tripo_pipeline.py --status   # what exists
  .venv/bin/python scripts/tripo_pipeline.py --dry-run  # cost estimate only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV = Path("/Users/jonathanlupo/Desktop/kendra/.env.local")
OUT = ROOT / "data" / "tripo"
STATE = OUT / "state.json"
CUTOUT = ROOT / "dashboard" / "public" / "kendra-reference-cutout.png"
BASE = "https://api.tripo3d.ai/v2/openapi"

# The animation set. Deliberately small: her expression engine already has
# 23 gestures, and blending a few well-chosen clips reads as alive far more
# cheaply than one clip per feeling.
ANIMATIONS = [
    "preset:idle",      # breathing base — everything else blends over this
    "preset:walk",      # movement commands and curiosity approach
    "preset:climb",     # leaning in: listening, close attention
    "preset:jump",      # delight, celebration, excited bounce
    "preset:run",       # urgency, "come here" at speed
    "preset:dance",     # singing sway and her dance behaviour
    "preset:hurt",      # recoil: surprise, "stop", startled
]


def load_key() -> str:
    match = re.search(r"^TRIPO_SECRET=(.+)$", ENV.read_text(), re.M)
    if not match:
        sys.exit("TRIPO_SECRET missing from .env.local")
    return match.group(1).strip()


class Tripo:
    def __init__(self, key: str):
        self.h = {"Authorization": f"Bearer {key}"}
        self.client = httpx.Client(timeout=120)

    def balance(self) -> float:
        r = self.client.get(f"{BASE}/user/balance", headers=self.h)
        r.raise_for_status()
        return float(r.json()["data"]["balance"])

    def upload(self, path: Path) -> str:
        r = self.client.post(
            f"{BASE}/upload", headers=self.h,
            files={"file": (path.name, path.read_bytes(), "image/png")},
        )
        r.raise_for_status()
        return r.json()["data"]["image_token"]

    def submit(self, body: dict) -> str:
        r = self.client.post(
            f"{BASE}/task", headers={**self.h, "Content-Type": "application/json"}, json=body,
        )
        if r.status_code == 403 and "credit" in r.text:
            sys.exit(f"OUT OF CREDITS — Tripo says: {r.json().get('message')}")
        r.raise_for_status()
        return r.json()["data"]["task_id"]

    def wait(self, task_id: str, label: str, timeout: float = 900) -> dict:
        """Poll until the task finishes. Tripo jobs run minutes, not seconds."""
        started = time.time()
        while time.time() - started < timeout:
            r = self.client.get(f"{BASE}/task/{task_id}", headers=self.h)
            r.raise_for_status()
            data = r.json()["data"]
            status = data.get("status")
            if status == "success":
                print(f"  {label}: done in {time.time() - started:.0f}s")
                return data
            if status in {"failed", "cancelled", "banned", "expired"}:
                sys.exit(f"  {label}: {status} — {json.dumps(data)[:300]}")
            print(f"  {label}: {status} {data.get('progress', 0)}%", end="\r")
            time.sleep(5)
        sys.exit(f"  {label}: timed out after {timeout}s")

    def download(self, url: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream("GET", url) as r:
            r.raise_for_status()
            with target.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        return target


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def write_state(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    state = read_state()
    if args.status:
        print(json.dumps(state, indent=2) if state else "nothing generated yet")
        return 0

    tripo = Tripo(load_key())
    balance = tripo.balance()
    planned = (0 if state.get("model_task") else 1) + (0 if state.get("rig_task") else 1) \
        + len([a for a in ANIMATIONS if a not in state.get("animations", {})])
    print(f"balance {balance:g} credits | {planned} operation(s) planned")
    if args.dry_run:
        print("dry run — nothing submitted")
        return 0
    if balance <= 0:
        sys.exit("No credits available. Claim the free tier at platform.tripo3d.ai, "
                 "or add credit, then run this again — it resumes where it stopped.")

    # 1. Model ------------------------------------------------------------
    if not state.get("model_task"):
        token = state.get("image_token") or tripo.upload(CUTOUT)
        state["image_token"] = token
        write_state(state)
        print("generating her model…")
        task = tripo.submit({
            "type": "image_to_model",
            "file": {"type": "png", "file_token": token},
            "texture": True,
            "pbr": True,
        })
        # Checkpoint BEFORE waiting: an interrupt during the poll used to
        # orphan a task that was already costing credits.
        state["model_task"] = task
        write_state(state)
        result = tripo.wait(task, "model")
        state["model_url"] = (result.get("output") or {}).get("pbr_model") \
            or (result.get("output") or {}).get("model")
        write_state(state)
        tripo.download(state["model_url"], OUT / "kendra.glb")
        print(f"  saved {OUT / 'kendra.glb'}")

    # 2. Rig --------------------------------------------------------------
    if not state.get("rig_task"):
        print("rigging her…")
        task = tripo.submit({
            "type": "animate_rig",
            "original_model_task_id": state["model_task"],
            "out_format": "glb",
        })
        # Checkpoint BEFORE waiting: an interrupt during the poll used to
        # orphan a task that was already costing credits.
        state["rig_task"] = task
        write_state(state)
        result = tripo.wait(task, "rig")
        state["rigged_url"] = (result.get("output") or {}).get("model")
        write_state(state)
        tripo.download(state["rigged_url"], OUT / "kendra-rigged.glb")
        print(f"  saved {OUT / 'kendra-rigged.glb'}")

    # 3. Animations -------------------------------------------------------
    state.setdefault("animations", {})
    for animation in ANIMATIONS:
        if animation in state["animations"]:
            continue
        print(f"animating {animation}…")
        task = tripo.submit({
            "type": "animate_retarget",
            "original_model_task_id": state["rig_task"],
            "animation": animation,
            "out_format": "glb",
        })
        state["animations"][animation] = {"task": task, "url": None}
        write_state(state)
        result = tripo.wait(task, animation)
        url = (result.get("output") or {}).get("model")
        state["animations"][animation]["url"] = url
        write_state(state)
        name = animation.replace("preset:", "")
        tripo.download(url, OUT / f"kendra-{name}.glb")
        print(f"  saved kendra-{name}.glb")

    print(f"\ndone — {len(state['animations'])} animations, "
          f"{tripo.balance():g} credits left")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
