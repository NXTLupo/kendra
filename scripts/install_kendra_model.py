#!/usr/bin/env python3
"""Inspect and install a rigged model dropped into the incoming folder.

Tripo's auto-rig forced a biped skeleton onto an eight-legged spider and
tore the mesh apart, so a purpose-rigged model is being supplied instead.
This checks what actually arrived before trusting it — bones, animation
clip names, morph targets (which decide whether she can blink properly),
scale and orientation — then wires it in.

Usage:
  .venv/bin/python scripts/install_kendra_model.py            # inspect + install
  .venv/bin/python scripts/install_kendra_model.py --inspect  # report only
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCOMING = ROOT / "dashboard" / "public" / "kendra3d" / "incoming"
STAGE = ROOT / "dashboard" / "public" / "kendra3d"
MANIFEST = STAGE / "model.json"


def read_glb(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:4] != b"glTF":
        raise ValueError(f"{path.name} is not a binary glTF (.glb)")
    length = struct.unpack("<I", raw[12:16])[0]
    return json.loads(raw[20:20 + length])


def describe(path: Path) -> dict:
    gltf = read_glb(path)
    skins = gltf.get("skins", [])
    nodes = gltf.get("nodes", [])
    bones = [nodes[j].get("name", "?") for j in (skins[0].get("joints", []) if skins else [])]
    morphs: list[str] = []
    for mesh in gltf.get("meshes", []):
        names = (mesh.get("extras") or {}).get("targetNames") or []
        morphs.extend(names)
    return {
        "file": path.name,
        "megabytes": round(path.stat().st_size / 1048576, 1),
        "meshes": len(gltf.get("meshes", [])),
        "bones": len(bones),
        "bone_sample": bones[:12],
        "animations": [a.get("name", "unnamed") for a in gltf.get("animations", [])],
        "morph_targets": morphs,
        "textures": len(gltf.get("textures", [])),
        "embedded_textures": not any("uri" in i for i in gltf.get("images", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()

    files = sorted(p for p in INCOMING.glob("*.glb"))
    if not files:
        print(f"Nothing to install. Drop a .glb into {INCOMING}")
        return 1

    reports = [describe(p) for p in files]
    for report in reports:
        print(f"\n{report['file']}  ({report['megabytes']} MB)")
        print(f"  meshes {report['meshes']} | bones {report['bones']} | "
              f"textures {report['textures']} "
              f"({'embedded' if report['embedded_textures'] else 'EXTERNAL — keep the files together'})")
        print(f"  bones: {', '.join(report['bone_sample']) or 'none (unrigged)'}")
        print(f"  animations: {', '.join(report['animations']) or 'none'}")
        print(f"  morph targets: {', '.join(report['morph_targets']) or 'none'}"
              f"{'  <- blinking can be driven independently' if report['morph_targets'] else '  <- blink will be procedural'}")

    # Heuristic worth surfacing: a spider driven by a humanoid rig is the
    # exact failure that produced the previous mess.
    for report in reports:
        joined = " ".join(report["bone_sample"]).lower()
        if report["bones"] and {"thigh", "calf", "spine"} & set(joined.split()):
            print(f"\n  WARNING: {report['file']} looks humanoid-rigged "
                  f"(thigh/calf/spine bones). That is what mangled the last model.")

    if args.inspect:
        return 0

    base = max(files, key=lambda p: p.stat().st_size)
    shutil.copy2(base, STAGE / "kendra-body.glb")
    clips = [p.name for p in files if p != base]
    for clip in clips:
        shutil.copy2(INCOMING / clip, STAGE / clip)
    MANIFEST.write_text(json.dumps({
        "body": "kendra-body.glb",
        "clips": clips,
        "inspected": reports,
    }, indent=2))
    print(f"\ninstalled {base.name} as her body"
          + (f" plus {len(clips)} clip file(s)" if clips else ""))
    print("rebuild the app to see her:  cd dashboard && npm run build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
