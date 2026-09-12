"""Continuing a clip: the tail that gets anchored, and the route that cuts it.

The join is the whole feature, and the thing that makes it a join rather than a
cut is that the new clip opens on the old one's last frames *and their sound*.
That makes the tail frame-exact by requirement, not by preference: MiniMaxH3AddGuide
takes the first N frames of whatever it is handed, so a tail that is off by a few
frames anchors the wrong window and leaves a visible gap at the seam.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.sinks import OVERLAP_FRAMES, tail_clip_bytes
from app.store import jobs
from tests.conftest import sign_in

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _render(path: Path, seconds: int = 5, rate: int = 24, audio: bool = True) -> bytes:
    """A clip shaped like one H3 produces: 1344x768, 24fps, with sound."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size=1344x768:rate={rate}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run([*cmd, str(path)], check=True)
    return path.read_bytes()


def _count(data: bytes, streams: str, entries: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "probe.mp4"
        p.write_bytes(data)
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", streams, "-count_packets",
             "-show_entries", entries, "-of", "csv=p=0", str(p)], capture_output=True)
        return r.stdout.decode().strip()


def _frames(data: bytes) -> int:
    return int(_count(data, "v:0", "stream=nb_read_packets") or 0)


def _has_audio(data: bytes) -> bool:
    return bool(_count(data, "a:0", "stream=index"))


# ---------------------------------------------------------------- the tail

@needs_ffmpeg
def test_the_tail_is_exactly_the_length_the_model_anchors(tmp_path):
    tail = tail_clip_bytes(_render(tmp_path / "in.mp4"))
    assert tail is not None
    assert _frames(tail) == OVERLAP_FRAMES


@needs_ffmpeg
def test_the_tail_keeps_its_sound(tmp_path):
    """Without it the join is silent for its first second, or crashes the render."""
    tail = tail_clip_bytes(_render(tmp_path / "in.mp4"))
    assert tail is not None and _has_audio(tail)


@needs_ffmpeg
def test_a_silent_clip_still_comes_back_with_an_audio_track(tmp_path):
    """H3 reads the guide's audio; an absent track is a crash on a rented GPU."""
    tail = tail_clip_bytes(_render(tmp_path / "in.mp4", audio=False))
    assert tail is not None
    assert _frames(tail) == OVERLAP_FRAMES and _has_audio(tail)


@needs_ffmpeg
def test_a_faster_source_is_resampled_rather_than_shortened(tmp_path):
    """22 frames of 30fps footage is three quarters of a second, and the
    continuation would carry on at the wrong speed."""
    tail = tail_clip_bytes(_render(tmp_path / "in.mp4", rate=30))
    assert tail is not None
    assert _frames(tail) == OVERLAP_FRAMES


@needs_ffmpeg
def test_the_tail_is_a_small_fraction_of_the_clip(tmp_path):
    """It travels through the orchestrator, which owns a billing GPU."""
    data = _render(tmp_path / "in.mp4", seconds=15)
    tail = tail_clip_bytes(data)
    assert tail is not None and len(tail) < len(data) / 2


def test_something_that_is_not_a_video_fails_loudly():
    """The other helpers return the input on failure, because they protect a clip
    already paid for. This one decides whether a render still to come is right."""
    assert tail_clip_bytes(b"not a video at all") is None


# ---------------------------------------------------------------- the overlap

@needs_ffmpeg
async def test_a_finished_extension_has_its_overlap_removed(s3, tmp_path):
    """The render opens by reproducing the tail it continued. Kept, the two clips
    would repeat a second when laid end to end; that is what this takes off."""
    from app.sinks.object_store import ObjectSink
    sink = ObjectSink(s3, keep_audio=True)
    rendered = _render(tmp_path / "render.mp4", seconds=5)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p", "mode": "extend"},
                         rendered, "c.mp4")
    assert _frames(await s3.get(key)) == _frames(rendered) - OVERLAP_FRAMES


@needs_ffmpeg
async def test_every_other_mode_is_stored_exactly_as_rendered(s3, tmp_path):
    from app.sinks.object_store import ObjectSink
    sink = ObjectSink(s3, keep_audio=True)
    rendered = _render(tmp_path / "render.mp4", seconds=5)
    key = await sink.put({"id": "j2", "user_id": "u1", "prompt": "p", "mode": "i2v"},
                         rendered, "c.mp4")
    assert await s3.get(key) == rendered


def test_a_render_that_cannot_be_trimmed_is_kept_whole():
    """The opposite rule to the tail cut: this runs after the money is spent."""
    from app.sinks import drop_leading_frames
    assert drop_leading_frames(b"not a video") == b"not a video"


# ---------------------------------------------------------------- the route

async def _finished_clip(client, user, tmp_path, **over) -> str:
    store = client._transport.app.state.storage
    jid = await jobs.add(user["id"], "the clip being continued")
    key = f"videos/{user['id']}/{jid}.mp4"
    await store.put(key, _render(tmp_path / f"{jid}.mp4"), "video/mp4")
    await jobs.update(jid, status="done", output_key=key, **over)
    return jid


@needs_ffmpeg
async def test_a_finished_clip_yields_a_tail_of_my_own(client, db, s3, tmp_path):
    u = await sign_in(client)
    jid = await _finished_clip(client, u, tmp_path)

    r = await client.post(f"/api/jobs/{jid}/extend-source")
    assert r.status_code == 200, r.text
    body = r.json()
    # An upload key of the caller's own, so the job that continues it is checked
    # by the same prefix filter as every other input.
    assert body["key"].startswith(f"uploads/{u['id']}/") and body["key"].endswith(".mp4")

    store = client._transport.app.state.storage
    assert _frames(await store.get(body["key"])) == OVERLAP_FRAMES


@needs_ffmpeg
async def test_the_tail_can_be_queued_as_a_job_that_continues_the_clip(client, db, s3, tmp_path):
    u = await sign_in(client)
    jid = await _finished_clip(client, u, tmp_path)
    key = (await client.post(f"/api/jobs/{jid}/extend-source")).json()["key"]

    r = await client.post("/api/jobs", json={
        "prompts": "she turns and walks away", "mode": "extend", "ref_images": [key]})
    assert r.status_code == 200, r.text
    queued = [j for j in await jobs.list_for(u["id"]) if j["mode"] == "extend"]
    assert len(queued) == 1 and queued[0]["ref_images"] == [key]


@needs_ffmpeg
async def test_the_source_clip_says_what_size_to_continue_it_at(client, db, s3, tmp_path):
    """The guide is centre-cropped to the render size, so continuing a draft clip
    at final quality would continue a cropped version of it."""
    u = await sign_in(client)
    jid = await _finished_clip(client, u, tmp_path, preset="draft", seconds=6)
    body = (await client.post(f"/api/jobs/{jid}/extend-source")).json()
    assert body["preset"] == "draft" and body["seconds"] == 6


async def test_a_clip_that_never_finished_has_nothing_to_continue(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "still queued")
    assert (await client.post(f"/api/jobs/{jid}/extend-source")).status_code == 404


async def test_extend_needs_a_source(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "mode": "extend"})
    assert r.status_code == 400
    assert "video" in r.json()["detail"]


# ---------------------------------------------------------------- uploads

@needs_ffmpeg
async def test_an_uploaded_video_is_stored_as_just_its_tail(client, db, s3, tmp_path):
    """A whole film is not kept to use one second of it, and re-encoding means
    nothing from the original file reaches the bucket."""
    u = await sign_in(client)
    data = _render(tmp_path / "phone.mp4", seconds=8, rate=30)
    r = await client.post("/api/upload/video",
                          files={"file": ("phone.mp4", data, "video/mp4")})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert key.startswith(f"uploads/{u['id']}/") and key.endswith(".mp4")

    stored = await client._transport.app.state.storage.get(key)
    assert _frames(stored) == OVERLAP_FRAMES
    assert len(stored) < len(data)


async def test_a_file_that_is_not_a_video_is_refused(client, db):
    await sign_in(client)
    r = await client.post("/api/upload/video",
                          files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


async def test_a_video_that_cannot_be_read_is_refused(client, db):
    await sign_in(client)
    r = await client.post("/api/upload/video",
                          files={"file": ("broken.mp4", b"not really an mp4", "video/mp4")})
    assert r.status_code == 400


async def test_an_oversized_video_is_refused(client, db, monkeypatch):
    from app.routes import media as media_routes
    monkeypatch.setattr(media_routes, "MAX_VIDEO_BYTES", 1024)
    await sign_in(client)
    r = await client.post("/api/upload/video",
                          files={"file": ("big.mp4", b"x" * 4096, "video/mp4")})
    assert r.status_code == 413


@needs_ffmpeg
async def test_an_uploaded_video_can_be_queued_as_an_extension(client, db, s3, tmp_path):
    u = await sign_in(client)
    data = _render(tmp_path / "phone.mp4", seconds=6)
    key = (await client.post("/api/upload/video",
                             files={"file": ("phone.mp4", data, "video/mp4")})).json()["key"]
    r = await client.post("/api/jobs", json={
        "prompts": "the camera keeps moving", "mode": "extend", "ref_images": [key]})
    assert r.status_code == 200, r.text
    assert [j["ref_images"] for j in await jobs.list_for(u["id"])] == [[key]]
