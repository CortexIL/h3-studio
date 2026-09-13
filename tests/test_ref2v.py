"""References: the ref2va checkpoint and a node that takes lists.

Images, whole short videos and audio clips, each named in the prompt by a tag
in the order it was connected. A different 21 GB model; the same everything else.
"""
from __future__ import annotations

import shutil

import pytest

from app import modes
from app.config import Config
from app.orchestrator import Orchestrator
from app.sinks import reference_video_bytes
from app.store import jobs, users
from app.workflows import R2V_NODE, REF2VA_MODEL, build_workflow, validate_graph
from tests.conftest import sign_in
from tests.fakes import FakeBackend, FakeSink, FakeStorage
from tests.test_extend import _render, _frames, _has_audio

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _graph(**job):
    base = {"prompt": "<Picture 1> speaks", "mode": "r2v", "preset": "final", "seconds": 5}
    return build_workflow({**base, **job}, Config())


def _node(graph, cls):
    return next(n for n in graph.values() if n.get("class_type") == cls)


def test_references_is_offered_again():
    assert "r2v" in modes.OFFERED and modes.label("r2v") == "References"


def test_the_reference_node_replaces_the_image_node_and_the_checkpoint_swaps():
    g = _graph(ref_images=["u/face.png"], ref_video_names=["move.mp4"], ref_audio_names=["voice.wav"])
    node = _node(g, R2V_NODE)["inputs"]
    assert "first_frame" not in node and node["ref_image_size"] == "match" and "audio_vae" in node
    assert node["ref_images.ref_image_0"] == ["h3_ref_img_0", 0]
    assert node["ref_videos.ref_video_0"] == ["h3_ref_vid_parts_0", 0]
    assert node["ref_video_audios.ref_video_audio_0"] == ["h3_ref_vid_parts_0", 1]
    assert node["ref_audios.ref_audio_0"] == ["h3_ref_aud_0", 0]
    assert _node(g, "UNETLoader")["inputs"]["unet_name"] == REF2VA_MODEL
    assert _node(g, "LoadVideo")["inputs"]["file"] == "move.mp4"
    assert _node(g, "LoadAudio")["inputs"]["audio"] == "voice.wav"
    assert not any(n.get("class_type") == "MiniMaxH3ImageToVideo" for n in g.values())
    validate_graph(g)


def test_references_are_numbered_from_zero_in_the_order_given():
    g = _graph(ref_images=["u/a.png", "u/b.png", "u/c.png"])
    node = _node(g, R2V_NODE)["inputs"]
    assert [node[f"ref_images.ref_image_{i}"][0] for i in range(3)] == ["h3_ref_img_0", "h3_ref_img_1", "h3_ref_img_2"]
    assert [g[f"h3_ref_img_{i}"]["inputs"]["image"] for i in range(3)] == ["a.png", "b.png", "c.png"]


def test_turbo_uses_the_reference_checkpoints_own_lora():
    g = _graph(preset="turbo", ref_images=["u/a.png"])
    lora = _node(g, "LoraLoaderModelOnly")["inputs"]["lora_name"]
    assert lora == "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
    assert _node(build_workflow({"prompt": "p", "mode": "i2v", "preset": "turbo",
                                 "ref_images": ["u/a.png"]}, Config()), "LoraLoaderModelOnly")["inputs"]["lora_name"].startswith("minimax_h3_fl2v")


def test_the_checkpoint_and_its_lora_are_in_the_manifest():
    names = {f.src.rsplit("/", 1)[-1] for f in Config().weights.files}
    assert REF2VA_MODEL.rsplit("/", 1)[-1] in names
    assert "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" in names


# ---- the route ----

async def test_reference_media_is_stored_owned_and_capped(client, db):
    u = await sign_in(client)
    mine = lambda n: f"uploads/{u['id']}/{n}"
    r = await client.post("/api/jobs", json={
        "prompts": "<Picture 1> walks", "mode": "r2v", "ref_images": [mine("a.png")],
        "ref_videos": [mine("v1.mp4"), "uploads/other/v.mp4", mine("v2.mp4"), mine("v3.mp4"), mine("v4.mp4")],
        "ref_audios": [mine("s.wav")]})
    assert r.status_code == 200, r.text
    row = (await jobs.list_for(u["id"]))[0]
    assert row["ref_videos"] == [mine("v1.mp4"), mine("v2.mp4"), mine("v3.mp4")]
    assert row["ref_audios"] == [mine("s.wav")]
    listed = (await client.get("/api/jobs")).json()["jobs"][0]
    assert listed["ref_videos"] == row["ref_videos"] and listed["ref_audios"] == [mine("s.wav")]


async def test_references_need_at_least_one_of_anything(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "mode": "r2v"})
    assert r.status_code == 400 and "at least one" in r.json()["detail"]


async def test_reference_media_is_only_for_references(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "mode": "i2v",
                                             "ref_videos": [f"uploads/{u['id']}/v.mp4"]})
    assert r.status_code == 400


async def test_use_again_keeps_the_reference_media(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p", mode="r2v", ref_videos=[f"uploads/{u['id']}/v.mp4"],
                         ref_audios=[f"uploads/{u['id']}/s.wav"])
    await jobs.update(jid, status="done")
    copy = await jobs.get_any((await client.post(f"/api/jobs/{jid}/again")).json()["job_id"])
    assert copy["ref_videos"] == [f"uploads/{u['id']}/v.mp4"] and copy["ref_audios"] == [f"uploads/{u['id']}/s.wav"]


# ---- the upload ----

@needs_ffmpeg
def test_a_reference_video_is_shrunk_to_the_size_the_model_reads(tmp_path):
    out = reference_video_bytes(_render(tmp_path / "big.mp4", seconds=3, rate=30))
    assert out is not None and _has_audio(out)
    assert _frames(out) == 72       # 3 s at 24 fps


@needs_ffmpeg
def test_a_silent_reference_video_gets_a_soundtrack(tmp_path):
    out = reference_video_bytes(_render(tmp_path / "mute.mp4", seconds=2, audio=False))
    assert out is not None and _has_audio(out)


def test_junk_is_not_a_reference_video():
    assert reference_video_bytes(b"not a video") is None


@needs_ffmpeg
async def test_the_upload_route_stores_a_reference_video_under_the_caller(client, db, s3, tmp_path):
    u = await sign_in(client)
    r = await client.post("/api/upload/refvideo",
                          files={"file": ("move.mp4", _render(tmp_path / "m.mp4", seconds=2), "video/mp4")})
    assert r.status_code == 200, r.text
    assert r.json()["key"].startswith(f"uploads/{u['id']}/")


# ---- the orchestrator ----

async def test_reference_media_travels_to_the_gpu_and_the_graph_gets_the_stored_names(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    v, a = f"uploads/{u['id']}/move.mp4", f"uploads/{u['id']}/voice.wav"
    await jobs.add(u["id"], "<Video 1>", mode="r2v", ref_videos=[v], ref_audios=[a])
    backend = FakeBackend()
    o = Orchestrator(Config.from_settings(app_settings), backend, FakeSink(),
                     FakeStorage({v: b"MP4", a: b"RIFF"}))
    await o.start(run_loop=False)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert backend.uploaded == [(b"MP4", "move.mp4"), (b"RIFF", "voice.wav")]
        sent = backend.submitted[0]
        assert sent["ref_video_names"] == ["move.mp4"] and sent["ref_audio_names"] == ["voice.wav"]
    finally:
        await o.stop()
