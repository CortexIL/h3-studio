from __future__ import annotations

from app import storage as storage_mod
from app.store import jobs, users
from tests.conftest import sign_in

CLIP = bytes(range(256)) * 8       # 2048 bytes, every value distinct per block


async def _my_finished_clip(client, user) -> tuple[str, str]:
    key = storage_mod.video_key(user["id"], "j1", "a-clip")
    await client._transport.app.state.storage.put(key, CLIP, "video/mp4")
    jid = await jobs.add(user["id"], "a clip")
    await jobs.update(jid, status="done", output_key=key, finished_at=1.0,
                      output_bytes=len(CLIP))
    return jid, key


async def test_upload_returns_a_key_under_my_prefix(client, db):
    u = await sign_in(client)
    r = await client.post("/api/upload",
                          files={"file": ("ref.png", b"imagebytes", "image/png")})
    assert r.status_code == 200
    assert r.json()["key"].startswith(f"uploads/{u['id']}/")


async def test_upload_refuses_a_non_image(client, db):
    await sign_in(client)
    r = await client.post("/api/upload",
                          files={"file": ("notes.txt", b"x", "text/plain")})
    assert r.status_code == 400


async def test_i_can_read_back_my_own_upload(client, db):
    await sign_in(client)
    key = (await client.post(
        "/api/upload",
        files={"file": ("ref.png", b"imagebytes", "image/png")})).json()["key"]
    r = await client.get(f"/api/image/{key}")
    assert r.status_code == 200 and r.content == b"imagebytes"


async def test_video_streams_the_whole_clip(client, db):
    u = await sign_in(client)
    jid, _ = await _my_finished_clip(client, u)
    r = await client.get(f"/api/video/{jid}")
    assert r.status_code == 200
    assert r.content == CLIP
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4"


async def test_video_honours_a_range_so_the_player_can_seek(client, db):
    u = await sign_in(client)
    jid, _ = await _my_finished_clip(client, u)
    r = await client.get(f"/api/video/{jid}", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == CLIP[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(CLIP)}"
    assert r.headers["content-length"] == "10"


async def test_a_job_with_no_output_is_404(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "still queued")
    assert (await client.get(f"/api/video/{jid}")).status_code == 404


async def test_a_clip_deleted_from_the_store_is_404_not_a_crash(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "gone")
    await jobs.update(jid, status="done", output_key="videos/x/missing.mp4",
                      finished_at=1.0)
    assert (await client.get(f"/api/video/{jid}")).status_code == 404


async def test_another_users_clip_is_404_even_though_the_bytes_exist(client, db):
    victim = await users.create("victim@h3.local", "passphrase-9")
    await sign_in(client, "attacker@h3.local", password="passphrase-2")
    key = storage_mod.video_key(victim["id"], "j9", "theirs")
    await client._transport.app.state.storage.put(key, CLIP, "video/mp4")
    jid = await jobs.add(victim["id"], "theirs")
    await jobs.update(jid, status="done", output_key=key, finished_at=1.0)
    r = await client.get(f"/api/video/{jid}")
    assert r.status_code == 404
    assert CLIP not in r.content


# ---- B7: images keep their real type and are cacheable ----

async def test_an_uploaded_jpeg_comes_back_as_jpeg(client, db):
    await sign_in(client)
    key = (await client.post(
        "/api/upload", files={"file": ("ref.jpg", b"\xff\xd8\xff-jpeg", "image/jpeg")}
    )).json()["key"]
    r = await client.get(f"/api/image/{key}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"]
