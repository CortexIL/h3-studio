"""Lip-sync from your audio: a voice track the clip follows.

The guide node takes audio at frame 0 and the model syncs mouth, timing and
expression to it. No new model: the same weights, one more anchored input.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import Config
from app.orchestrator import Orchestrator
from app.sinks import audio_track_bytes
from app.store import jobs, users
from app.workflows import AUDIO_GUIDE_ID, AUDIO_LOADER_ID, KEYFRAME_GUIDE_ID, build_workflow, validate_graph
from tests.conftest import sign_in
from tests.fakes import FakeBackend, FakeSink, FakeStorage

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone(path: Path, seconds: int = 20) -> bytes:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "libmp3lame", str(path)],
                   check=True)
    return path.read_bytes()


def _guider(graph):
    return next(n for n in graph.values() if n.get("class_type") == "BasicGuider")


# ---- the graph ----

def test_the_track_is_loaded_and_anchored_at_the_first_frame():
    g = build_workflow({"prompt": "p", "mode": "i2v", "preset": "final", "seconds": 5,
                        "ref_images": ["u/face.png"], "audio_key": "u/line.wav",
                        "audio_name": "line.wav"}, Config())
    assert g[AUDIO_LOADER_ID] == {"class_type": "LoadAudio",
                                  "_meta": {"title": "Load Audio (the track the clip follows)"},
                                  "inputs": {"audio": "line.wav"}}
    guide = g[AUDIO_GUIDE_ID]["inputs"]
    assert guide["audio"] == [AUDIO_LOADER_ID, 0] and guide["frame_idx"] == 0
    assert "image" not in guide and "audio_vae" in guide
    assert _guider(g)["inputs"]["conditioning"] == [AUDIO_GUIDE_ID, 0]
    validate_graph(g)


def test_the_track_and_keyframes_chain_together():
    g = build_workflow({"prompt": "p", "mode": "t2v", "preset": "final", "seconds": 5,
                        "audio_key": "u/line.wav", "keyframes": [{"key": "u/a.png", "at": 2}]}, Config())
    assert g[KEYFRAME_GUIDE_ID.format(i=0)]["inputs"]["positive"] == [AUDIO_GUIDE_ID, 0]
    assert _guider(g)["inputs"]["conditioning"] == [KEYFRAME_GUIDE_ID.format(i=0), 0]
    validate_graph(g)


# ---- the route ----

async def test_a_track_is_stored_returned_and_copied_by_use_again(client, db):
    u = await sign_in(client)
    key = f"uploads/{u['id']}/line.wav"
    r = await client.post("/api/jobs", json={"prompts": "she speaks", "audio": key, "keep_audio": True})
    assert r.status_code == 200, r.text
    row = (await jobs.list_for(u["id"]))[0]
    assert row["audio_key"] == key
    assert (await client.get("/api/jobs")).json()["jobs"][0]["audio"] == key
    await jobs.update(row["id"], status="done")
    copy = await jobs.get_any((await client.post(f"/api/jobs/{row['id']}/again")).json()["job_id"])
    assert copy["audio_key"] == key


async def test_somebody_elses_track_is_refused(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "audio": "uploads/other/line.wav"})
    assert r.status_code == 400 and "not one of yours" in r.json()["detail"]


async def test_extend_cannot_take_a_track(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "mode": "extend", "ref_images": [f"uploads/{u['id']}/tail.mp4"],
        "audio": f"uploads/{u['id']}/line.wav"})
    assert r.status_code == 400 and "extend" in r.json()["detail"]


async def test_a_track_with_sound_off_is_refused(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "audio": f"uploads/{u['id']}/line.wav", "keep_audio": False})
    assert r.status_code == 400 and "sound switch" in r.json()["detail"]


# ---- the upload ----

@needs_ffmpeg
def test_any_audio_becomes_a_short_stereo_wav(tmp_path):
    out = audio_track_bytes(_tone(tmp_path / "line.mp3"))
    assert out is not None and out[:4] == b"RIFF"
    # 16 s at 48 kHz stereo 16-bit, plus a header
    assert 3_000_000 < len(out) < 3_200_000


def test_junk_is_refused_loudly():
    assert audio_track_bytes(b"not audio at all") is None


@needs_ffmpeg
async def test_the_upload_route_stores_the_track_under_the_caller(client, db, tmp_path):
    u = await sign_in(client)
    data = _tone(tmp_path / "line.mp3", seconds=3)
    r = await client.post("/api/upload/audio", files={"file": ("line.mp3", data, "audio/mpeg")})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert key.startswith(f"uploads/{u['id']}/") and key.endswith(".wav")


async def test_a_file_that_is_not_audio_is_refused(client, db):
    await sign_in(client)
    r = await client.post("/api/upload/audio", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


# ---- the orchestrator ----

async def test_the_track_travels_to_the_gpu_under_its_own_name(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    key = f"uploads/{u['id']}/line.wav"
    await jobs.add(u["id"], "p", mode="t2v", audio_key=key)
    backend = FakeBackend()
    o = Orchestrator(Config.from_settings(app_settings), backend, FakeSink(), FakeStorage({key: b"RIFFwav"}))
    await o.start(run_loop=False)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert backend.uploaded == [(b"RIFFwav", "line.wav")]
        assert backend.submitted[0]["audio_name"] == "line.wav"
    finally:
        await o.stop()
