"""Upscale: a finished clip enlarged by Real-ESRGAN on the same pod.

No diffusion runs. The clip is cut into chunks, each goes through the upscale
model, and the chunks are joined back in a balanced tree so memory stays flat.
"""
from __future__ import annotations

import shutil

import pytest

from app import modes
from app.config import Config
from app.store import jobs
from app.workflows import UPSCALE_CHUNK, UPSCALE_MODEL, build_workflow, validate_graph
from tests.conftest import sign_in
from tests.test_extend import _render

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _graph(frames):
    return build_workflow({"mode": "upscale", "ref_images": ["videos/u/clip.mp4"],
                           "source_frames": frames}, Config())


def _by_class(graph, cls):
    return [n for n in graph.values() if n.get("class_type") == cls]


def test_upscale_is_legal_in_the_database_but_never_offered():
    assert "upscale" in modes.KNOWN and "upscale" not in modes.OFFERED
    assert modes.label("upscale") == "Upscale"


def test_a_long_clip_is_cut_into_chunks_and_joined_in_a_tree():
    g = _graph(124)
    slices = _by_class(g, "ImageFromBatch")
    assert [(s["inputs"]["batch_index"], s["inputs"]["length"]) for s in slices] == \
        [(0, 32), (32, 32), (64, 32), (96, 28)]
    assert len(_by_class(g, "ImageUpscaleWithModel")) == 4
    joins = _by_class(g, "ImageBatch")
    assert len(joins) == 3                      # 4 chunks -> 2 -> 1
    # the video reads the last join; the sound and the frame rate come from the source
    video = _by_class(g, "CreateVideo")[0]["inputs"]
    assert video["images"] == ["u_cat_2", 0] and video["audio"] == ["u_parts", 1] and video["fps"] == ["u_parts", 2]
    assert _by_class(g, "UpscaleModelLoader")[0]["inputs"]["model_name"] == UPSCALE_MODEL
    assert _by_class(g, "LoadVideo")[0]["inputs"]["file"] == "clip.mp4"
    validate_graph(g)


def test_a_short_clip_goes_through_in_one_pass():
    g = _graph(UPSCALE_CHUNK)
    assert not _by_class(g, "ImageFromBatch") and not _by_class(g, "ImageBatch")
    assert _by_class(g, "ImageUpscaleWithModel")[0]["inputs"]["image"] == ["u_parts", 0]
    validate_graph(g)


def test_an_odd_number_of_chunks_still_joins_completely():
    g = _graph(UPSCALE_CHUNK * 5)
    assert len(_by_class(g, "ImageBatch")) == 4    # 5 -> 3 -> 2 -> 1
    validate_graph(g)


def test_the_upscale_model_is_in_the_manifest():
    cfg = Config()
    files = {f.src.rsplit("/", 1)[-1]: f for f in cfg.weights.files}
    assert files[UPSCALE_MODEL].repo == "fofr/comfyui" and files[UPSCALE_MODEL].dst == "upscale_models"


def test_the_delivery_presets_are_hidden_from_the_picker():
    cfg = Config().generation
    assert cfg.presets["up2x"].hidden and cfg.presets["hd1080up"].hidden
    assert (cfg.presets["hd1080up"].output_width, cfg.presets["hd1080up"].output_height) == (1920, 1080)
    assert not cfg.presets["final"].hidden


async def _finished(client, user, tmp_path, **over):
    store = client._transport.app.state.storage
    jid = await jobs.add(user["id"], "a lantern", seconds=5, **over)
    key = f"videos/{user['id']}/{jid}.mp4"
    await store.put(key, _render(tmp_path / f"{jid}.mp4"), "video/mp4")
    await jobs.update(jid, status="done", output_key=key)
    return jid, key


@needs_ffmpeg
async def test_a_finished_clip_becomes_an_upscale_job_with_its_frames_counted(client, db, s3, tmp_path):
    u = await sign_in(client)
    jid, key = await _finished(client, u, tmp_path, keep_audio=False)
    r = await client.post(f"/api/jobs/{jid}/upscale", json={"deliver": "1080p"})
    assert r.status_code == 200, r.text
    new = await jobs.get_any(r.json()["job_id"])
    assert new["mode"] == "upscale" and new["preset"] == "hd1080up"
    assert new["ref_images"] == [key] and new["source_job_id"] == jid
    assert new["source_frames"] == 120 and r.json()["frames"] == 120
    assert new["keep_audio"] is False and new["upscale_factor"] == 2
    # the card is not pointed at a video it cannot show
    listed = (await client.get("/api/jobs")).json()["jobs"][0]
    assert listed["ref_images"] == [] and listed["source_job_id"] == jid


async def test_a_clip_that_never_finished_cannot_be_upscaled(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "still queued")
    assert (await client.post(f"/api/jobs/{jid}/upscale", json={"deliver": "2x"})).status_code == 404


async def test_an_unknown_delivery_is_refused(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    assert (await client.post(f"/api/jobs/{jid}/upscale", json={"deliver": "8k"})).status_code == 400


async def test_the_composer_cannot_queue_an_upscale_directly(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "mode": "upscale"})
    assert r.status_code == 400
