"""Sound direction: the soundscape and music lines a clip carries.

H3 renders the soundtrack in the same pass as the picture and reads a separate
soundscape and music description far better than sound words buried in the shot.
They live in their own columns so they come back into their own fields.
"""
from __future__ import annotations

from app.config import Config
from app.store import jobs
from app.workflows import assemble_prompt, build_workflow
from tests.conftest import sign_in


async def test_direction_is_stored_with_the_clip_and_returned(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "a lantern on a stone wall", "keep_audio": True,
        "sound": "  wind in olive leaves, a lantern creaking ", "music": "slow solo piano"})
    assert r.status_code == 200, r.text
    row = (await jobs.list_for(u["id"]))[0]
    assert row["sound"] == "wind in olive leaves, a lantern creaking"
    assert row["music"] == "slow solo piano"
    listed = (await client.get("/api/jobs")).json()["jobs"][0]
    assert listed["sound"] == row["sound"] and listed["music"] == "slow solo piano"


async def test_an_empty_field_is_stored_as_nothing(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "sound": "   ", "music": ""})
    assert r.status_code == 200, r.text
    row = (await jobs.list_for(u["id"]))[0]
    assert row["sound"] is None and row["music"] is None


def test_the_prompt_the_model_sees_has_labelled_sections():
    text = assemble_prompt({"prompt": "a lantern on a wall", "sound": "wind", "music": "piano"})
    assert text == "a lantern on a wall\n\nAudio: wind\n\nMusic: piano"


def test_a_clip_without_direction_sends_the_prompt_untouched():
    assert assemble_prompt({"prompt": "a lantern on a wall"}) == "a lantern on a wall"
    assert assemble_prompt({"prompt": "p", "sound": None, "music": ""}) == "p"


def test_the_graph_carries_the_assembled_prompt():
    graph = build_workflow({"prompt": "a lantern", "sound": "wind", "mode": "t2v",
                            "preset": "final", "seconds": 5}, Config())
    prompts = [n["inputs"]["prompt"] for n in graph.values()
               if n.get("class_type", "").startswith("MiniMaxH3")]
    assert prompts and prompts[0] == "a lantern\n\nAudio: wind"


async def test_use_again_keeps_the_direction(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p", sound="wind", music="piano")
    await jobs.update(jid, status="done")
    r = await client.post(f"/api/jobs/{jid}/again")
    assert r.status_code == 200, r.text
    copy = await jobs.get_any(r.json()["job_id"])
    assert copy["sound"] == "wind" and copy["music"] == "piano"
