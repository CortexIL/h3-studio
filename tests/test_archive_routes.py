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


# ---- downloading several clips as one zip ----

def _names(payload: bytes) -> list[str]:
    import io
    import zipfile
    return zipfile.ZipFile(io.BytesIO(payload)).namelist()


async def test_several_clips_come_back_as_one_zip(client, db):
    u = await sign_in(client)
    first, _ = await _stored_clip(client, u, prompt="a red car")
    second, _ = await _stored_clip(client, u, prompt="a lighthouse")

    r = await client.get(f"/api/archive/download?ids={first},{second}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    assert sorted(_names(r.content)) == sorted(
        [f"a-red-car-{first}.mp4", f"a-lighthouse-{second}.mp4"])


async def test_the_zip_carries_the_real_bytes(client, db):
    import io
    import zipfile
    u = await sign_in(client)
    jid, _ = await _stored_clip(client, u)
    r = await client.get(f"/api/archive/download?ids={jid}")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.read(zf.namelist()[0]) == b"x" * 2048


async def test_another_persons_clip_is_simply_not_in_the_zip(client, db):
    """The id resolves through the caller's own scoped read, so it yields that
    clip's absence rather than its contents."""
    other = await users.create("b@h3.local", "passphrase-2")
    theirs = await jobs.add(other["id"], "not yours")
    await jobs.update(theirs, status="done", output_key="videos/x/nope.mp4")

    u = await sign_in(client)
    mine, _ = await _stored_clip(client, u, prompt="mine")
    r = await client.get(f"/api/archive/download?ids={theirs},{mine}")
    assert r.status_code == 200
    assert _names(r.content) == [f"mine-{mine}.mp4"]


async def test_a_download_of_nothing_but_other_peoples_clips_is_a_404(client, db):
    other = await users.create("c@h3.local", "passphrase-2")
    theirs = await jobs.add(other["id"], "not yours")
    await jobs.update(theirs, status="done", output_key="videos/x/nope.mp4")
    await sign_in(client)
    assert (await client.get(f"/api/archive/download?ids={theirs}")).status_code == 404


async def test_one_missing_clip_does_not_sink_the_whole_batch(client, db):
    """Losing a batch of thirty over one deleted clip is the worse outcome."""
    u = await sign_in(client)
    mine, _ = await _stored_clip(client, u, prompt="kept")
    r = await client.get(f"/api/archive/download?ids=doesnotexist,{mine}")
    assert r.status_code == 200
    assert _names(r.content) == [f"kept-{mine}.mp4"]


async def test_asking_for_no_clips_is_refused(client, db):
    await sign_in(client)
    assert (await client.get("/api/archive/download?ids=")).status_code == 400


async def test_the_same_clip_twice_appears_once(client, db):
    """A zip with two identical entry names is a zip half the tools mis-read."""
    u = await sign_in(client)
    jid, _ = await _stored_clip(client, u)
    r = await client.get(f"/api/archive/download?ids={jid},{jid}")
    assert len(_names(r.content)) == 1


# ---- deleting a selection ----


async def test_a_selection_is_deleted_files_and_all(client, db):
    u = await sign_in(client)
    store = client._transport.app.state.storage
    first, first_key = await _stored_clip(client, u, "one")
    second, second_key = await _stored_clip(client, u, "two")
    kept, kept_key = await _stored_clip(client, u, "three")

    r = await client.post("/api/archive/delete", json={"ids": [first, second]})
    assert r.status_code == 200 and r.json() == {"deleted": 2, "kept": 0}
    assert await store.head(first_key) is None
    assert await store.head(second_key) is None
    assert await jobs.get_for(u["id"], first) is None
    # Nothing outside the selection is touched.
    assert await store.head(kept_key) is not None
    assert await jobs.get_for(u["id"], kept) is not None


async def test_a_selection_cannot_reach_somebody_else_s_clips(client, db):
    other = await users.create("b@h3.local", "passphrase-2")
    theirs = await jobs.add(other["id"], "not yours")
    await jobs.update(theirs, status="done", output_key="videos/x.mp4", finished_at=1.0)
    u = await sign_in(client)
    mine, _ = await _stored_clip(client, u, "mine")

    r = await client.post("/api/archive/delete", json={"ids": [theirs, mine]})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert await jobs.get_for(other["id"], theirs) is not None


async def test_an_id_that_is_already_gone_does_not_fail_the_rest(client, db):
    u = await sign_in(client)
    mine, _ = await _stored_clip(client, u, "mine")
    r = await client.post("/api/archive/delete", json={"ids": ["doesnotexist", mine]})
    assert r.status_code == 200 and r.json()["deleted"] == 1


async def test_deleting_nothing_is_a_mistake_worth_reporting(client, db):
    await sign_in(client)
    assert (await client.post("/api/archive/delete", json={"ids": []})).status_code == 400
    assert (await client.post("/api/archive/delete",
                              json={"ids": ["  "]})).status_code == 400


async def test_a_repeated_id_is_deleted_once(client, db):
    u = await sign_in(client)
    mine, _ = await _stored_clip(client, u, "mine")
    r = await client.post("/api/archive/delete", json={"ids": [mine, mine]})
    assert r.json() == {"deleted": 1, "kept": 0}

