"""The archive's own actions: search, filter, and deleting a clip for good."""
from __future__ import annotations

from app import storage as storage_mod
from app.store import jobs, users
from tests.conftest import sign_in


async def _stored_clip(client, user, prompt="a clip", preset="final"):
    store = client._transport.app.state.storage
    jid = await jobs.add(user["id"], prompt, preset=preset)
    key = storage_mod.video_key(user["id"], jid, "clip")
    await store.put(key, b"x" * 2048, "video/mp4")
    await jobs.update(jid, status="done", output_key=key, finished_at=1.0,
                      output_bytes=2048)
    return jid, key


async def test_deleting_a_clip_removes_the_file_and_the_entry(client, db):
    u = await sign_in(client)
    store = client._transport.app.state.storage
    jid, key = await _stored_clip(client, u)
    r = await client.delete(f"/api/archive/{jid}")
    assert r.status_code == 200
    assert await store.head(key) is None
    assert await jobs.get_for(u["id"], jid) is None
    assert (await client.get(f"/api/video/{jid}")).status_code == 404


async def test_only_finished_clips_can_be_deleted_from_the_archive(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "still queued")
    assert (await client.delete(f"/api/archive/{jid}")).status_code == 404
    assert await jobs.get_for(u["id"], jid) is not None


async def test_archive_search_and_preset_filter_over_http(client, db):
    u = await sign_in(client)
    red, _ = await _stored_clip(client, u, "Red car", preset="turbo")
    await _stored_clip(client, u, "blue boat", preset="final")
    body = (await client.get("/api/archive", params={"q": "red"})).json()
    assert [c["id"] for c in body["clips"]] == [red]
    body = (await client.get("/api/archive", params={"preset": "final"})).json()
    assert len(body["clips"]) == 1 and body["clips"][0]["id"] != red


async def test_an_overlong_search_is_rejected(client, db):
    await sign_in(client)
    assert (await client.get("/api/archive", params={"q": "x" * 201})).status_code == 422


async def test_archive_clips_carry_no_storage_keys(client, db):
    u = await sign_in(client)
    await _stored_clip(client, u)
    assert "videos/" not in (await client.get("/api/archive")).text
