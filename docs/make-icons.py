"""Build the home-screen icons from web/logo.png.

The logo is a rounded square on transparency. A home-screen icon must be
full-bleed: iOS and Android apply their own mask, so rounded corners baked into
the file come out masked twice, with dark wedges in the corners. So the gradient
is re-fitted from the logo's own pixels, painted edge to edge, and the black
monogram is laid back over it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

im = Image.open(SRC).convert("RGBA")
W, H = im.size
px = im.load()
SPAN = 2.0 * (max(W, H) - 1)


def lum_of(r: int, g: int, b: int) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


# Fit colour = c0 + c1 * t, t = (x + y) / span, over the lit part of the logo
# only: opaque, and not the monogram. The gradient runs top-left to
# bottom-right, so one term does it. Normal equations, three channels at once.
n = st = stt = 0.0
sc = [0.0, 0.0, 0.0]
sct = [0.0, 0.0, 0.0]
ink_n = 0
ink_sum = [0.0, 0.0, 0.0]
ink_lum = 0.0
for y in range(H):
    for x in range(W):
        r, g, b, alpha = px[x, y]
        if alpha <= 250:
            continue
        lu = lum_of(r, g, b)
        if lu > 100:
            t = (x + y) / SPAN
            n += 1
            st += t
            stt += t * t
            for i, c in enumerate((r, g, b)):
                sc[i] += c
                sct[i] += c * t
        elif lu < 60:
            ink_n += 1
            ink_lum += lu
            for i, c in enumerate((r, g, b)):
                ink_sum[i] += c

det = n * stt - st * st
c0 = [(sc[i] * stt - sct[i] * st) / det for i in range(3)]
c1 = [(n * sct[i] - sc[i] * st) / det for i in range(3)]
ink = tuple(int(round(ink_sum[i] / ink_n)) for i in range(3))
glyph_lum = ink_lum / ink_n
print(f"gradient {tuple(round(v) for v in c0)} -> "
      f"{tuple(round(c0[i] + c1[i]) for i in range(3))}, ink {ink}")


def bg_at(t: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(round(c0[i] + c1[i] * t)))) for i in range(3))


# The monogram as coverage: 1 where solid, feathered where it was anti-aliased
# against the gradient under it.
cover = Image.new("L", (W, H), 0)
cpx = cover.load()
x0, y0, x1, y1 = W, H, -1, -1
for y in range(H):
    for x in range(W):
        r, g, b, alpha = px[x, y]
        if alpha == 0:
            continue
        br, bg_, bb = bg_at((x + y) / SPAN)
        base = lum_of(br, bg_, bb)
        v = (base - lum_of(r, g, b)) / (base - glyph_lum)
        v = 0.0 if v < 0 else 1.0 if v > 1 else v
        v *= alpha / 255.0
        if v <= 0.002:
            continue
        cpx[x, y] = int(round(v * 255))
        if v > 0.5:
            x0, y0 = min(x0, x), min(y0, y)
            x1, y1 = max(x1, x), max(y1, y)

glyph = cover.crop((x0, y0, x1 + 1, y1 + 1))
NATIVE_SHARE = glyph.width / W
print(f"monogram {glyph.size} at ({x0},{y0}) in {im.size}, "
      f"{NATIVE_SHARE:.3f} of the width")


def build(size: int, share: float) -> Image.Image:
    """One icon: the gradient edge to edge, the monogram centred at `share` of the width."""
    out = Image.new("RGB", (size, size))
    opx = out.load()
    span = 2.0 * (size - 1)
    row = [bg_at(i / span) for i in range(2 * size - 1)]
    for y in range(size):
        for x in range(size):
            opx[x, y] = row[x + y]
    gw = max(1, round(size * share))
    gh = max(1, round(gw * glyph.height / glyph.width))
    mask = glyph.resize((gw, gh), Image.LANCZOS)
    out.paste(Image.new("RGB", (gw, gh), ink), ((size - gw) // 2, (size - gh) // 2), mask)
    return out


# "any" icons are full-bleed; the launcher rounds them. A maskable icon may be
# cropped to a circle, so its monogram is pulled well inside the safe zone.
for name, size, share in [
    ("apple-touch-icon.png", 180, NATIVE_SHARE),
    ("icon-192.png", 192, NATIVE_SHARE),
    ("icon-512.png", 512, NATIVE_SHARE),
    ("icon-maskable-512.png", 512, NATIVE_SHARE * 0.72),
]:
    img = build(size, share)
    img.save(OUT / name, format="PNG", optimize=True)
    print(f"{name}: {img.size} {(OUT / name).stat().st_size} bytes")
