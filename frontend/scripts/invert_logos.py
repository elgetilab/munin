#!/usr/bin/env python3
"""Invert Munin logos (dark → white) for use on dark backgrounds."""

from PIL import Image, ImageOps
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "static/landing/assets"

logos = [
    ("munin_logo_with_script.webp", "munin_logo_with_script_white.webp"),
    ("munin_logo_without_script.webp", "munin_logo_without_script_white.webp"),
]

for src_name, dst_name in logos:
    src = ASSETS / src_name
    dst = ASSETS / dst_name

    img = Image.open(src).convert("RGBA")

    # Split into channels
    r, g, b, a = img.split()

    # Invert only RGB, keep alpha unchanged
    rgb = Image.merge("RGB", (r, g, b))
    rgb_inverted = ImageOps.invert(rgb)

    # Recombine with original alpha
    ri, gi, bi = rgb_inverted.split()
    result = Image.merge("RGBA", (ri, gi, bi, a))

    result.save(dst, "WEBP", quality=95)
    print(f"Created: {dst}")