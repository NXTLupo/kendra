#!/usr/bin/env python3
"""Build Kendra's app icon from the owner-supplied reference image.

Crops to Kendra's face -- the two big reflective eyes are the only part of her
that survives being scaled down to a 32-pixel Dock tile -- and lays it out with
modern macOS icon geometry: a rounded square inset inside a transparent canvas.

Build-time only, and macOS-only. Nothing here runs on the Raspberry Pi; the
robot has no Dock. Requires Pillow, which is deliberately not a Kendra runtime
dependency:

    .venv/bin/python -m pip install Pillow
    .venv/bin/python scripts/make_kendra_icon.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dashboard/public/kendra-reference.png"

# Face centre and half-width in source pixels, measured against the 506x740
# reference. Centres her head with a small margin and drops the desert.
FACE_CENTER = (246, 320)
FACE_HALF = 132

CANVAS = 1024
# macOS Big Sur icon geometry: the rounded square covers ~82% of the canvas and
# the rest is transparent breathing room.
INSET_RATIO = 0.82
CORNER_RATIO = 0.225


def build_master() -> object:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    if not SOURCE.is_file():
        raise SystemExit(f"Missing Kendra reference image: {SOURCE}")

    source = Image.open(SOURCE).convert("RGB")
    cx, cy = FACE_CENTER
    face = source.crop((cx - FACE_HALF, cy - FACE_HALF, cx + FACE_HALF, cy + FACE_HALF))

    tile = round(CANVAS * INSET_RATIO)
    face = face.resize((tile, tile), Image.LANCZOS)

    # A little more contrast and colour so her teal reads against a dark Dock,
    # and a touch of sharpening so the eyes stay crisp when downscaled.
    face = ImageEnhance.Color(face).enhance(1.12)
    face = ImageEnhance.Contrast(face).enhance(1.08)
    face = face.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))

    # Rounded-square mask, supersampled so the corners are not jagged.
    scale = 4
    radius = round(tile * CORNER_RATIO)
    mask = Image.new("L", (tile * scale, tile * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, tile * scale - 1, tile * scale - 1),
        radius=radius * scale,
        fill=255,
    )
    mask = mask.resize((tile, tile), Image.LANCZOS)

    icon = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    offset = (CANVAS - tile) // 2

    # Soft drop shadow so the tile has depth on both light and dark desktops.
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 90), (offset, offset + round(tile * 0.02)), mask)
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=CANVAS * 0.012)))

    face_rgba = face.convert("RGBA")
    face_rgba.putalpha(mask)
    icon.alpha_composite(face_rgba, (offset, offset))
    return icon


def write_icns(icon, iconset: Path, icns: Path) -> None:
    from PIL import Image

    if iconset.exists():
        for item in iconset.iterdir():
            item.unlink()
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        icon.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
        icon.resize((size * 2, size * 2), Image.LANCZOS).save(
            iconset / f"icon_{size}x{size}@2x.png"
        )
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--icns", type=Path, default=ROOT / "dashboard/public/kendra.icns")
    parser.add_argument("--png", type=Path, default=ROOT / "dashboard/public/kendra-icon.png")
    parser.add_argument("--preview", type=Path, default=None)
    args = parser.parse_args()

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Pillow is required: .venv/bin/python -m pip install Pillow", file=sys.stderr)
        return 2

    icon = build_master()
    args.png.parent.mkdir(parents=True, exist_ok=True)
    icon.save(args.png)
    write_icns(icon, ROOT / "build/kendra.iconset", args.icns)
    print(f"icon png : {args.png}")
    print(f"icon icns: {args.icns}")

    if args.preview:
        from PIL import Image

        sizes = [512, 256, 128, 64, 32, 16]
        sheet = Image.new("RGBA", (sum(sizes) + 20 * len(sizes), 540), (245, 241, 234, 255))
        x = 10
        for size in sizes:
            sheet.alpha_composite(icon.resize((size, size), Image.LANCZOS), (x, 10))
            x += size + 20
        sheet.convert("RGB").save(args.preview)
        print(f"preview  : {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
