"""Regenerate PWA icons: python scripts/make_icons.py (needs Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
BG = (15, 17, 21)
TRACK = (42, 47, 58)
ARCS = [((217, 119, 87), 0.72), ((63, 182, 139), 0.45)]  # claude-ish orange, codex-ish green


def draw(size: int, pad_ratio: float, rounded: bool) -> Image.Image:
    s = size * 4  # supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=BG)
    else:
        d.rectangle([0, 0, s, s], fill=BG)
    pad = s * pad_ratio
    width = int(s * 0.085)
    for i, (color, frac) in enumerate(ARCS):
        inset = pad + i * width * 1.6
        box = [inset, inset, s - inset, s - inset]
        d.arc(box, 0, 360, fill=TRACK, width=width)
        d.arc(box, -90, -90 + 360 * frac, fill=color, width=width)
    return img.resize((size, size), Image.LANCZOS)


OUT.mkdir(parents=True, exist_ok=True)
draw(192, 0.16, True).save(OUT / "icon-192.png")
draw(512, 0.16, True).save(OUT / "icon-512.png")
draw(512, 0.26, False).save(OUT / "icon-maskable-512.png")
draw(180, 0.14, False).convert("RGB").save(OUT / "apple-touch-icon.png")  # iOS adds its own rounding
(OUT / "icon.svg").write_text(
    """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect width="100" height="100" rx="22" fill="#0f1115"/>
<g fill="none" stroke-width="8.5" stroke-linecap="butt" transform="rotate(-90 50 50)">
<circle cx="50" cy="50" r="29.75" stroke="#2a2f3a"/>
<circle cx="50" cy="50" r="29.75" stroke="#d97757" stroke-dasharray="134.6 200"/>
<circle cx="50" cy="50" r="16.15" stroke="#2a2f3a"/>
<circle cx="50" cy="50" r="16.15" stroke="#3fb68b" stroke-dasharray="45.7 200"/>
</g></svg>
"""
)
print("icons written to", OUT)
