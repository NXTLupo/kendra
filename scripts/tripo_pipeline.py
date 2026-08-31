#!/usr/bin/env python3
"""Generate, rig and animate Kendra's 3D model through Tripo.

Runs the whole chain in one command so the credit budget is spent
deliberately rather than by trial and error:

    upload cutout -> image-to-model -> RIG-CHECK -> rig -> animations -> GLBs

Talks to **v3** (`openapi.tripo3d.ai`), not the retiring v2. See
docs/TRIPO_API.md; v3 lives on a different host, which is why earlier v3
probes against the v2 host all 404'd.

The rig-check step is free and is the important one. On v2 the rigger was
biped-only and forced a humanoid skeleton onto an eight-legged model,
which tore the mesh apart. v3 has a `hexapod` rig type; rig-check reports
which type to ask for before a single credit is spent.

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
import shutil
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV = Path("/Users/jonathanlupo/Desktop/kendra/.env.local")
OUT = ROOT / "data" / "tripo"
STATE = OUT / "state.json"
CUTOUT = ROOT / "dashboard" / "public" / "kendra-reference-cutout.png"
# Where the app actually reads her body from. The pipeline used to stop at
# data/tripo, so a finished rig never reached the screen: you could spend the
# credits, get a correct skeleton on disk, and see no change at all.
STAGE = ROOT / "dashboard" / "public" / "kendra3d"
DIST = ROOT / "dashboard" / "dist" / "kendra3d"
BASE = "https://openapi.tripo3d.ai/v3"

# The rigger to ask for. v1.0 is biped-only; anything with more than two
# legs needs v2.5, or the skeleton comes back humanoid.
RIG_MODEL_NONHUMANOID = "v2.5-20260210"

# Presets are per rig type, and the non-humanoid libraries are ONE clip
# each — there is no hexapod greeting, song or emotion at any price, and
# blinking never exists because Tripo emits no morph targets. Everything
# expressive stays procedural in dashboard/src/kendraStage.ts; what Tripo
# contributes is a correct skeleton and a correct walk.
PRESETS: dict[str, list[str]] = {
    "hexapod": ["preset:hexapod:walk"],
    "octopod": ["preset:octopod:walk"],
    "quadruped": ["preset:quadruped:walk"],
    "serpentine": ["preset:serpentine:march"],
    "aquatic": ["preset:aquatic:march"],
    # Biped is the one rich library. Kept in case rig-check says biped.
    "biped": [
        "preset:idle", "preset:walk", "preset:run",
        "preset:climb", "preset:jump", "preset:hurt",
    ],
}


def load_key() -> str:
    match = re.search(r"^TRIPO_SECRET=(.+)$", ENV.read_text(), re.M)
    if not match:
        sys.exit("TRIPO_SECRET missing from .env.local")
    return match.group(1).strip()


class Tripo:
    def __init__(self, key: str):
        self.h = {"Authorization": f"Bearer {key}"}
        self.client = httpx.Client(timeout=120)

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        """One request, retried on 429.

        Tripo rate-limits per API key and limits concurrency per account,
        and both surface as 429. It tells you exactly how long to wait, so
        honour that before falling back to exponential backoff.
        """
        # Merge rather than pass headers twice: callers add Content-Type, and
        # supplying `headers` here as well made httpx reject the call.
        headers = {**self.h, **(kw.pop("headers", None) or {})}
        for attempt in range(5):
            r = self.client.request(method, f"{BASE}{path}", headers=headers, **kw)
            if r.status_code != 429:
                return r
            retry_after = r.headers.get("Retry-After")
            reset_at = r.headers.get("X-RateLimit-Reset")
            if retry_after:
                wait = int(retry_after)
            elif reset_at:
                wait = max(int(reset_at) - int(time.time()), 1)
            else:
                wait = 2 ** attempt
            print(f"  rate limited, waiting {wait}s")
            time.sleep(min(wait, 32))
        sys.exit("Rate limited five times in a row — try again later.")

    def balance(self) -> float:
        r = self._send("GET", "/account/balance")
        r.raise_for_status()
        # Credits are decimals, not integers: the August 2026 changelog
        # exists to correct that. int() truncates fractional (VIP) credits.
        return float(r.json()["data"]["balance"])

    def upload(self, path: Path) -> str:
        r = self._send(
            "POST", "/files",
            files={"file": (path.name, path.read_bytes(), "image/png")},
        )
        r.raise_for_status()
        return r.json()["data"]["file_token"]

    def submit(self, path: str, body: dict) -> str:
        r = self._send(
            "POST", path,
            headers={**self.h, "Content-Type": "application/json"}, json=body,
        )
        if r.status_code == 403 and "credit" in r.text:
            sys.exit(f"OUT OF CREDITS — Tripo says: {r.json().get('message')}")
        r.raise_for_status()
        payload = r.json()
        if payload.get("code") != 0:
            sys.exit(f"{path} rejected: {payload.get('message')} — "
                     f"{payload.get('suggestion', '')}")
        return payload["data"]["task_id"]

    def wait(self, task_id: str, label: str, timeout: float = 900) -> dict:
        """Poll until the task finishes. Tripo jobs run minutes, not seconds."""
        started = time.time()
        while time.time() - started < timeout:
            r = self._send("GET", f"/tasks/{task_id}")
            r.raise_for_status()
            data = r.json()["data"]
            status = data.get("status")
            if status == "success":
                print(f"  {label}: done in {time.time() - started:.0f}s")
                return data
            if status in {"failed", "cancelled", "banned", "expired"}:
                # Frozen credits are returned on failure and cancellation,
                # so a dead task costs nothing.
                sys.exit(f"  {label}: {status} — {json.dumps(data)[:300]}")
            print(f"  {label}: {status} {data.get('progress', 0)}%", end="\r")
            time.sleep(5)
        sys.exit(f"  {label}: timed out after {timeout}s")

    def recent_tasks(self) -> list[dict]:
        """Tasks this account has run.

        v2 had no such endpoint, which is how an interrupted run once
        orphaned a task id and left credits frozen with no way to find the
        job again. This is the recovery path.
        """
        r = self._send("POST", "/tasks/list",
                       headers={**self.h, "Content-Type": "application/json"},
                       json={})
        if r.status_code != 200:
            return []
        return r.json().get("data", {}).get("tasks", []) or []

    def download(self, url: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream("GET", url) as r:
            r.raise_for_status()
            with target.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        return target


def model_url_of(result: dict) -> str | None:
    """v3 returns `model_url`; v2 returned `model` / `pbr_model`."""
    output = result.get("output") or {}
    return output.get("model_url") or output.get("pbr_model") or output.get("model")


def install_for_app(rigged: Path) -> None:
    """Put the rigged model where the app loads it, keeping the old one.

    Vite copies public/ into dist/ at build time, so a rig installed only
    into public/ stays invisible until the next rebuild. Write both when
    dist/ exists and she changes on the next app start, no rebuild needed.
    """
    for folder in (STAGE, DIST):
        if folder is DIST and not folder.exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "kendra-body.glb"
        if target.exists():
            # Never destroy the body she is currently wearing.
            shutil.copy2(target, folder / "kendra-body.previous.glb")
        shutil.copy2(rigged, target)
        print(f"  installed -> {target.relative_to(ROOT)}")
    print("  the previous body is kept alongside as kendra-body.previous.glb")


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
    parser.add_argument(
        "--rerig", action="store_true",
        help="Discard the existing rig and its clips and rig again. The first "
             "rig was made by v2, whose rigger was biped-only and forced a "
             "humanoid skeleton onto her; v3 rigs her as the octopod that "
             "rig-check reports. The MODEL is kept — only the rig is redone.",
    )
    args = parser.parse_args()

    state = read_state()
    if args.rerig:
        # Keep model_task and image_token: the model itself is fine, and it
        # was built from the background-removed cutout as intended. Only the
        # skeleton and everything retargeted onto it are wrong.
        stale = ("rig_task", "rigged_url", "rig_type", "rig_check_task")
        if args.dry_run:
            # --dry-run must never mutate. Report and leave the record alone.
            print(f"--rerig would discard rig {state.get('rig_task')} and "
                  f"{len(state.get('animations', {}))} clip(s); the model is kept")
        else:
            dropped = {k: state.pop(k, None) for k in stale}
            clips = state.pop("animations", {})
            write_state(state)
            print(f"cleared the old rig ({dropped.get('rig_task')}) and "
                  f"{len(clips)} clip(s); the model is kept")

    if args.status:
        print(json.dumps(state, indent=2) if state else "nothing generated yet")
        return 0

    tripo = Tripo(load_key())
    balance = tripo.balance()
    # The animation count is unknown until rig-check names the rig type, and
    # rig-check is free, so it is not counted as a billable operation.
    rig_type = state.get("rig_type")
    clips = PRESETS.get(rig_type, []) if rig_type else []
    planned = (
        (0 if state.get("model_task") else 1)
        + (0 if state.get("rig_task") else 1)
        + (1 if [a for a in clips if a not in state.get("animations", {})] else 0)
    )
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
        task = tripo.submit("/generation/image-to-model", {
            "input": token,
            "texture": True,
            "pbr": True,
        })
        # Checkpoint BEFORE waiting: an interrupt during the poll used to
        # orphan a task that was already costing credits.
        state["model_task"] = task
        write_state(state)
        result = tripo.wait(task, "model")
        state["model_url"] = model_url_of(result)
        write_state(state)
        tripo.download(state["model_url"], OUT / "kendra.glb")
        print(f"  saved {OUT / 'kendra.glb'}")

    # 2. Rig check — FREE, and the step that decides everything after it ---
    if not state.get("rig_type"):
        print("checking how she should be rigged…")
        task = tripo.submit("/animations/rig-check", {"input": state["model_task"]})
        state["rig_check_task"] = task
        write_state(state)
        output = tripo.wait(task, "rig-check").get("output") or {}
        if not output.get("riggable", False):
            sys.exit("Tripo says this model cannot be rigged. Nothing spent — "
                     "rig-check is free. Render it unrigged and animate "
                     "procedurally instead.")
        state["rig_type"] = output.get("rig_type") or "biped"
        write_state(state)
        print(f"  riggable as: {state['rig_type']}")

    rig_type = state["rig_type"]
    if rig_type not in PRESETS:
        sys.exit(f"No preset list for rig type {rig_type!r}; add one to PRESETS.")

    # 3. Rig ---------------------------------------------------------------
    if not state.get("rig_task"):
        print(f"rigging her as {rig_type}…")
        body = {
            "input": state["model_task"],
            "rig_type": rig_type,
            "out_format": "glb",
        }
        if rig_type != "biped":
            # The default rigger is biped-only; asking it for six legs is
            # what produced a humanoid skeleton on a spider.
            body["model"] = RIG_MODEL_NONHUMANOID
        task = tripo.submit("/animations/rig", body)
        state["rig_task"] = task
        write_state(state)
        result = tripo.wait(task, "rig")
        state["rigged_url"] = model_url_of(result)
        write_state(state)
        tripo.download(state["rigged_url"], OUT / "kendra-rigged.glb")
        print(f"  saved {OUT / 'kendra-rigged.glb'}")
        install_for_app(OUT / "kendra-rigged.glb")

    # 4. Animations --------------------------------------------------------
    state.setdefault("animations", {})
    wanted = [a for a in PRESETS[rig_type] if a not in state["animations"]]
    if wanted:
        # v3 retargets a whole list in ONE call. animate_in_place keeps her
        # on the spot: a walk cycle with root motion translates her out of
        # frame on a fixed stage.
        print(f"animating {len(wanted)} clip(s)…")
        task = tripo.submit("/animations/retarget", {
            "input": state["rig_task"],
            "animations": wanted,
            "out_format": "glb",
            "bake_animation": True,
            "animate_in_place": True,
        })
        for animation in wanted:
            state["animations"][animation] = {"task": task, "url": None}
        write_state(state)
        result = tripo.wait(task, "animations")
        url = model_url_of(result)
        for animation in wanted:
            state["animations"][animation]["url"] = url
        write_state(state)
        name = rig_type if len(wanted) > 1 else wanted[0].split(":")[-1]
        tripo.download(url, OUT / f"kendra-{name}.glb")
        print(f"  saved kendra-{name}.glb")

    print(f"\ndone — {len(state['animations'])} animation(s), "
          f"{tripo.balance():g} credits left")
    print("Expression, singing, greetings and blinking stay procedural: "
          "there are no hexapod presets for them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
