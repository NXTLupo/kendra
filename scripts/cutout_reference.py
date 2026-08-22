#!/usr/bin/env python3
"""Cut Kendra out of her reference image so Tripo sees only her.

Tripo's image-to-3D reconstructs whatever fills the frame; her reference
is a full desert scene with cacti and dunes, and those would end up
extruded into the mesh. rembg cannot build on this Python (llvmlite), so
this runs the same u2netp segmentation network directly on the
onnxruntime she already uses for ears, voice and memory — no new
dependency, ~4.5 MB model.

Usage:
  .venv/bin/python scripts/cutout_reference.py
Writes dashboard/public/kendra-reference-cutout.png (RGBA, subject only).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "u2net" / "u2netp.onnx"
SOURCE = ROOT / "dashboard" / "public" / "kendra-reference.png"
TARGET = ROOT / "dashboard" / "public" / "kendra-reference-cutout.png"
SIZE = 320


def main() -> int:
    image = Image.open(SOURCE).convert("RGB")
    small = image.resize((SIZE, SIZE), Image.LANCZOS)
    array = np.asarray(small, dtype=np.float32) / 255.0
    # u2net's published normalisation.
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    array = (array - mean) / std
    tensor = array.transpose(2, 0, 1)[None, ...]

    session = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
    mask = session.run(None, {session.get_inputs()[0].name: tensor})[0][0, 0]
    mask = (mask - mask.min()) / (mask.max() - mask.min() + 1e-8)

    alpha = Image.fromarray((mask * 255).astype(np.uint8)).resize(image.size, Image.LANCZOS)
    # Harden the edges: a soft matte leaves a halo that Tripo reconstructs
    # as a translucent shell around her.
    alpha_array = np.asarray(alpha, dtype=np.float32) / 255.0
    alpha_array = np.clip((alpha_array - 0.35) / 0.30, 0.0, 1.0)

    # Keep only her: the matte also caught a desert twig beside her legs,
    # and Tripo would have reconstructed it as part of her body.
    try:
        import cv2

        binary = (alpha_array > 0.5).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        if count > 1:
            largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            alpha_array = np.where(labels == largest, alpha_array, 0.0)
            removed = count - 2
            if removed > 0:
                print(f"removed {removed} stray fragment(s) from the matte")
    except Exception:
        pass

    cutout = np.dstack([np.asarray(image, dtype=np.uint8),
                        (alpha_array * 255).astype(np.uint8)])
    result = Image.fromarray(cutout, mode="RGBA")

    # Trim to her bounding box and pad: Tripo reconstructs the framed
    # subject, so filling the frame with her gives more mesh detail.
    box = result.getbbox()
    if box:
        result = result.crop(box)
    side = max(result.size) + 40
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(result, ((side - result.width) // 2, (side - result.height) // 2), result)

    canvas.save(TARGET)
    covered = float((alpha_array > 0.5).mean())
    print(f"wrote {TARGET.name}: {canvas.size[0]}x{canvas.size[1]} RGBA, "
          f"subject covers {covered:.0%} of the original frame")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
