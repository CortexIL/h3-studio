"""What a reference looks like by the time it leaves for the GPU.

One morning RunPod's proxy took uploads at 23 KB/s. A 3 MB frame needed two
minutes, the dispatch timed out three times, and the feed said "failed" with
no reason. The model only ever sees the render size, so that is what travels.
"""
from __future__ import annotations

import io

from PIL import Image

from app.sinks import MAX_REFERENCE_BYTES, fit_reference

RENDER = (1344, 768)


def noisy_png(w: int, h: int, mode: str = "RGB") -> bytes:
    """Noise compresses badly, so this is heavy the way a real frame is."""
    im = Image.effect_noise((w, h), 64).convert(mode)
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def test_a_heavy_frame_travels_as_a_jpeg_at_the_render_size():
    png = noisy_png(1672, 941)
    assert len(png) > MAX_REFERENCE_BYTES
    out, name = fit_reference(png, "frame.png", *RENDER)
    assert name == "frame.jpg"
    w, h = _size(out)
    # never below the render size in either direction, so the centre crop is unchanged
    assert w >= RENDER[0] and h == RENDER[1]
    assert len(out) < len(png) / 3


def test_a_light_frame_is_sent_exactly_as_it_is():
    im = Image.new("RGB", (400, 225), "grey")
    out = io.BytesIO()
    im.save(out, format="PNG")
    png = out.getvalue()
    assert fit_reference(png, "small.png", *RENDER) == (png, "small.png")


def test_a_heavy_frame_already_at_render_size_keeps_its_pixels():
    png = noisy_png(*RENDER)
    out, name = fit_reference(png, "exact.png", *RENDER)
    assert name == "exact.jpg" and _size(out) == RENDER


def test_a_frame_with_transparency_still_travels():
    png = noisy_png(1600, 900, mode="RGBA")
    out, name = fit_reference(png, "alpha.png", *RENDER)
    assert name == "alpha.jpg" and _size(out)[1] == RENDER[1]


def test_an_extension_tail_is_not_touched():
    """It is a video; the frames it carries are cut exactly and must stay so."""
    tail = b"\x00\x00\x00\x1cftypisom" + b"\x00" * MAX_REFERENCE_BYTES
    assert fit_reference(tail, "tail.mp4", *RENDER) == (tail, "tail.mp4")


def test_something_unreadable_is_sent_as_is():
    junk = b"not a picture" * 60_000
    assert fit_reference(junk, "ref.png", *RENDER) == (junk, "ref.png")
