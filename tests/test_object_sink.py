from __future__ import annotations

import shutil
import subprocess

import pytest

from app.sinks.object_store import ObjectSink


async def test_put_stores_under_the_owner(s3):
    sink = ObjectSink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "a red car"},
                         b"video-bytes", "clip.mp4")
    assert key == "videos/u1/j1_a-red-car.mp4"
    assert await s3.get(key) == b"video-bytes"


async def test_two_jobs_never_collide(s3):
    sink = ObjectSink(s3, keep_audio=True)
    a = await sink.put({"id": "j1", "user_id": "u1", "prompt": "same"}, b"1", "c.mp4")
    b = await sink.put({"id": "j2", "user_id": "u1", "prompt": "same"}, b"2", "c.mp4")
    assert a != b
    assert await s3.get(a) == b"1" and await s3.get(b) == b"2"


async def test_prompt_with_no_usable_characters_still_gets_a_key(s3):
    sink = ObjectSink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "!!!"}, b"x", "c.mp4")
    assert key == "videos/u1/j1_clip.mp4"


async def test_hebrew_prompts_survive_the_slug(s3):
    sink = ObjectSink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "מכונית אדומה"},
                         b"x", "c.mp4")
    assert key.startswith("videos/u1/j1_") and key.endswith(".mp4")
    assert await s3.get(key) == b"x"


def _clip_with_audio(path):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1:r=8",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(path)], check=True)
    return path.read_bytes()


def _has_audio(data: bytes) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", "-"],
        input=data, capture_output=True)
    return probe.stdout.strip() != b""


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_audio_is_stripped_before_upload(s3, tmp_path):
    data = _clip_with_audio(tmp_path / "in.mp4")
    assert _has_audio(data)
    sink = ObjectSink(s3, keep_audio=False)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p"}, data, "c.mp4")
    assert not _has_audio(await s3.get(key))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_audio_is_kept_when_asked(s3, tmp_path):
    data = _clip_with_audio(tmp_path / "in.mp4")
    sink = ObjectSink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p"}, data, "c.mp4")
    assert _has_audio(await s3.get(key))


def test_a_file_ffmpeg_cannot_read_comes_back_untouched():
    """Losing a clip the user paid for is worse than leaving audio on it."""
    from app.sinks import strip_audio_bytes
    assert strip_audio_bytes(b"not a video at all") == b"not a video at all"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_poster_is_a_jpeg_frame_stored_under_the_owner(s3, tmp_path):
    data = _clip_with_audio(tmp_path / "in.mp4")
    sink = ObjectSink(s3, keep_audio=True)
    key = await sink.poster({"id": "j1", "user_id": "u1"}, data)
    assert key == "posters/u1/j1.jpg"
    assert (await s3.get(key))[:2] == b"\xff\xd8"


async def test_poster_of_garbage_is_none_not_an_error(s3):
    sink = ObjectSink(s3, keep_audio=True)
    assert await sink.poster({"id": "j1", "user_id": "u1"}, b"not a video") is None


# ---------------------------------------------------------------- 720p output

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _render(path, size="1344x768", seconds=1):
    """A clip the size H3 actually renders for the hd720 preset, with sound."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(path)], check=True)
    return path.read_bytes()


def _size(data: bytes, tmp_path) -> str:
    probe_file = tmp_path / "probe.mp4"
    probe_file.write_bytes(data)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(probe_file)],
        capture_output=True)
    return r.stdout.decode().strip()


def _hd720():
    from app.config import Preset
    return {"hd720": Preset(width=1344, height=768, steps=30, output_width=1280, output_height=720),
            "final": Preset(width=1344, height=768, steps=30)}


@needs_ffmpeg
def test_conform_makes_exactly_1280x720_and_keeps_the_sound(tmp_path):
    from app.sinks import conform_bytes
    out = conform_bytes(_render(tmp_path / "in.mp4"), 1280, 720)
    assert _size(out, tmp_path) == "1280x720"
    assert _has_audio(out)


@needs_ffmpeg
async def test_an_hd720_clip_is_conformed_when_stored(s3, tmp_path):
    sink = ObjectSink(s3, keep_audio=True, presets=_hd720())
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p", "preset": "hd720"},
                         _render(tmp_path / "in.mp4"), "c.mp4")
    assert _size(await s3.get(key), tmp_path) == "1280x720"


@needs_ffmpeg
async def test_other_presets_are_stored_exactly_as_rendered(s3, tmp_path):
    data = _render(tmp_path / "in.mp4")
    sink = ObjectSink(s3, keep_audio=True, presets=_hd720())
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p", "preset": "final"},
                         data, "c.mp4")
    assert await s3.get(key) == data


def test_a_file_ffmpeg_cannot_conform_comes_back_untouched():
    """A clip at the render size beats no clip."""
    from app.sinks import conform_bytes
    assert conform_bytes(b"not a video at all", 1280, 720) == b"not a video at all"


def test_hd720_asks_the_model_for_its_native_widescreen_size():
    from app.config import Config
    from app.workflows import build_workflow
    graph = build_workflow({"mode": "i2v", "preset": "hd720", "prompt": "p", "seconds": 5,
                            "ref_images": ["uploads/u1/frame.png"]}, Config())
    selector = next(n for n in graph.values()
                    if isinstance(n, dict) and n.get("class_type") == "ResolutionSelector")
    assert selector["inputs"]["megapixels"] == 1.03
    assert selector["inputs"]["aspect_ratio"] == "16:9 (Widescreen)"
