#!/usr/bin/env python3
"""Generate PWA icons from the Munin logo."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
src = Image.open(ROOT / "static/landing/assets/munin_logo_without_script.webp").convert("RGBA")
out_dir = ROOT / "frontend/public/icons"
out_dir.mkdir(parents=True, exist_ok=True)

for size in [192, 512]:
    img = src.copy()
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = ((size - img.width) // 2, (size - img.height) // 2)
    canvas.paste(img, offset)
    path = out_dir / f"munin-{size}.png"
    canvas.save(str(path), "PNG")
    print(f"Created {path} ({img.width}x{img.height} in {size}x{size})")
