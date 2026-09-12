"""Sound as a per-clip choice.

It used to be one setting for the whole install, which is the wrong shape for it:
the audio H3 generates is real at thirty steps and unusable noise under the
four-step turbo LoRA, so the right answer changes from clip to clip rather than
from deployment to deployment.

A clip that never expressed a choice - every clip queued before the switch existed
- carries NULL and still follows the install, so nothing about old work is retold
as a decision nobody made.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.sinks.object_store import ObjectSink
from app.store import jobs
from tests.conftest import sign_in

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _render(path: Path, seconds: int = 1) -> bytes:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x192:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(path)], check=True)
    return path.read_bytes()


def _has_audio(data: bytes) -> bool:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "probe.mp4"
        p.write_bytes(data)
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=index", "-of", "csv=p=0", str(p)], capture_output=True)
        return bool(r.stdout.strip())


# ---------------------------------------------------------------- the choice sticks

async def test_a_clip_asked_for_without_sound_records_that(client, db):
    u = await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "p", "keep_audio": False})
    assert (await jobs.list_for(u["id"]))[0]["keep_audio"] is False


async def test_a_clip_asked_for_with_sound_records_that(client, db):
    u = await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "p", "keep_audio": True})
    assert (await jobs.list_for(u["id"]))[0]["keep_audio"] is True


async def test_saying_nothing_leaves_it_to_the_install(client, db):
    """The shape old rows already have, so they keep meaning what they meant."""
    u = await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "p"})
    assert (await jobs.list_for(u["id"]))[0]["keep_audio"] is None


async def test_the_browser_is_told_what_a_clip_chose(client, db):
    await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "p", "keep_audio": False})
    assert (await client.get("/api/jobs")).json()["jobs"][0]["keep_audio"] is False


async def test_running_it_again_keeps_the_same_answer(client, db):
    """Otherwise a silent take comes back with noise on it."""
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p", keep_audio=False)
    await jobs.update(jid, status="done")
    await client.post(f"/api/jobs/{jid}/again")
    assert [j["keep_audio"] for j in await jobs.list_for(u["id"])] == [False, False]


# ---------------------------------------------------------------- and is obeyed

@needs_ffmpeg
async def test_a_clip_that_asked_for_silence_is_stored_silent(s3, tmp_path):
    sink = ObjectSink(s3, keep_audio=True)          # the install would keep it
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p", "keep_audio": False},
                         _render(tmp_path / "in.mp4"), "c.mp4")
    assert not _has_audio(await s3.get(key))


@needs_ffmpeg
async def test_a_clip_that_asked_for_sound_keeps_it(s3, tmp_path):
    sink = ObjectSink(s3, keep_audio=False)         # the install would strip it
    key = await sink.put({"id": "j2", "user_id": "u1", "prompt": "p", "keep_audio": True},
                         _render(tmp_path / "in.mp4"), "c.mp4")
    assert _has_audio(await s3.get(key))


@needs_ffmpeg
async def test_a_clip_with_no_opinion_follows_the_install(s3, tmp_path):
    silent = ObjectSink(s3, keep_audio=False)
    key = await silent.put({"id": "j3", "user_id": "u1", "prompt": "p", "keep_audio": None},
                           _render(tmp_path / "in.mp4"), "c.mp4")
    assert not _has_audio(await s3.get(key))

    loud = ObjectSink(s3, keep_audio=True)
    key = await loud.put({"id": "j4", "user_id": "u1", "prompt": "p"},
                         _render(tmp_path / "in.mp4"), "c.mp4")
    assert _has_audio(await s3.get(key))
