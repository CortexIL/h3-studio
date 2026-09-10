from __future__ import annotations

import io
import json
import zipfile

from app.store import jobs
from tests.conftest import sign_in


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


async def test_a_txt_batch_queues_my_jobs(client, db):
    u = await sign_in(client)
    r = await client.post("/api/inbox/upload",
                          files={"file": ("b.txt", b"one\ntwo\n", "text/plain")})
    assert r.status_code == 200 and r.json()["queued"] == 2
    assert {j["prompt"] for j in await jobs.list_for(u["id"])} == {"one", "two"}


async def test_takes_are_expanded(client, db):
    u = await sign_in(client)
    body = json.dumps([{"prompt": "a", "takes": 3}]).encode()
    r = await client.post("/api/inbox/upload",
                          files={"file": ("b.json", body, "application/json")})
    assert r.json()["queued"] == 3
    assert len(await jobs.list_for(u["id"])) == 3


async def test_an_unknown_preset_falls_back_rather_than_failing(client, db):
    u = await sign_in(client)
    body = json.dumps([{"prompt": "a", "preset": "nonexistent"}]).encode()
    await client.post("/api/inbox/upload",
                      files={"file": ("b.json", body, "application/json")})
    assert (await jobs.list_for(u["id"]))[0]["preset"] == "final"


async def test_a_zip_attaches_its_images(client, db):
    u = await sign_in(client)
    archive = _zip({"b.json": json.dumps([{"prompt": "a", "image": "ref.png"}]).encode(),
                    "ref.png": b"imagebytes"})
    r = await client.post("/api/inbox/upload",
                          files={"file": ("b.zip", archive, "application/zip")})
    assert r.status_code == 200 and r.json()["queued"] == 1
    row = (await jobs.list_for(u["id"]))[0]
    assert row["ref_images"] and row["ref_images"][0].startswith(f"uploads/{u['id']}/")


async def test_a_missing_image_is_reported_not_fatal(client, db):
    await sign_in(client)
    archive = _zip({"b.json": json.dumps([{"prompt": "a", "image": "gone.png"}]).encode()})
    r = await client.post("/api/inbox/upload",
                          files={"file": ("b.zip", archive, "application/zip")})
    assert r.status_code == 200
    assert r.json()["queued"] == 1
    assert r.json()["missing_images"] == ["gone.png"]


async def test_a_bare_image_is_stored_and_queues_nothing(client, db):
    u = await sign_in(client)
    r = await client.post("/api/inbox/upload",
                          files={"file": ("ref.png", b"img", "image/png")})
    assert r.json()["queued"] == 0
    assert list(r.json()["images"].values())[0].startswith(f"uploads/{u['id']}/")
    assert await jobs.list_for(u["id"]) == []


async def test_an_unsupported_file_is_refused_with_a_readable_message(client, db):
    await sign_in(client)
    r = await client.post("/api/inbox/upload",
                          files={"file": ("notes.docx", b"x", "application/msword")})
    assert r.status_code == 400 and "batch files" in r.json()["detail"]


async def test_malformed_json_is_a_400_naming_the_file(client, db):
    await sign_in(client)
    r = await client.post("/api/inbox/upload",
                          files={"file": ("b.json", b"{nope", "application/json")})
    assert r.status_code == 400 and "b.json" in r.json()["detail"]


async def test_batch_upload_requires_a_session(client, db):
    r = await client.post("/api/inbox/upload",
                          files={"file": ("b.txt", b"one\n", "text/plain")})
    assert r.status_code == 401


async def test_one_users_batch_is_invisible_to_another(client, db):
    from app.store import users
    await sign_in(client)
    await client.post("/api/inbox/upload",
                      files={"file": ("b.txt", b"my secret batch\n", "text/plain")})
    other = await users.create("other@h3.local", "passphrase-9")
    assert await jobs.list_for(other["id"]) == []
