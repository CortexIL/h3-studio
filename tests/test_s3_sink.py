from __future__ import annotations

import shutil
import subprocess

import pytest

from app.sinks.s3 import S3Sink


async def test_put_stores_under_the_owner(s3):
    sink = S3Sink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "a red car"},
                         b"video-bytes", "clip.mp4")
    assert key == "videos/u1/j1_a-red-car.mp4"
    assert await s3.get(key) == b"video-bytes"


async def test_two_jobs_never_collide(s3):
    sink = S3Sink(s3, keep_audio=True)
    a = await sink.put({"id": "j1", "user_id": "u1", "prompt": "same"}, b"1", "c.mp4")
    b = await sink.put({"id": "j2", "user_id": "u1", "prompt": "same"}, b"2", "c.mp4")
    assert a != b
    assert await s3.get(a) == b"1" and await s3.get(b) == b"2"


async def test_prompt_with_no_usable_characters_still_gets_a_key(s3):
    sink = S3Sink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "!!!"}, b"x", "c.mp4")
    assert key == "videos/u1/j1_clip.mp4"


async def test_hebrew_prompts_survive_the_slug(s3):
    sink = S3Sink(s3, keep_audio=True)
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
    sink = S3Sink(s3, keep_audio=False)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p"}, data, "c.mp4")
    assert not _has_audio(await s3.get(key))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_audio_is_kept_when_asked(s3, tmp_path):
    data = _clip_with_audio(tmp_path / "in.mp4")
    sink = S3Sink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p"}, data, "c.mp4")
    assert _has_audio(await s3.get(key))


def test_a_file_ffmpeg_cannot_read_comes_back_untouched():
    """Losing a clip the user paid for is worse than leaving audio on it."""
    from app.sinks import strip_audio_bytes
    assert strip_audio_bytes(b"not a video at all") == b"not a video at all"
