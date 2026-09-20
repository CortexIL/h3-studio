from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile

import pytest

from app.config import Config
from app.store import jobs
from tests.conftest import sign_in

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone(seconds: float = 2) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-ac", "2",
         "-c:a", "pcm_s16le", "-f", "wav", "-"], check=True, capture_output=True).stdout


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
    # ... to the install's default preset, whatever it is set to.
    assert (await jobs.list_for(u["id"]))[0]["preset"] == Config().generation.default_preset


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


# ---- a lip-sync batch: one clip per line of the song, each with its own track ----

@needs_ffmpeg
async def test_a_zip_gives_each_clip_the_track_it_names(client, db):
    u = await sign_in(client)
    archive = _zip({"plan.json": json.dumps([
        {"prompt": "the first line", "audio": "seg01.wav"},
        {"prompt": "the second line", "audio": "seg02.wav"}]).encode(),
        "seg01.wav": _tone(), "seg02.wav": _tone(3)})
    r = await client.post("/api/inbox/upload",
                          files={"file": ("song.zip", archive, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 2 and len(r.json()["audios"]) == 2
    rows = sorted(await jobs.list_for(u["id"]), key=lambda j: j["prompt"])
    keys = [row["audio_key"] for row in rows]
    assert all(k and k.startswith(f"uploads/{u['id']}/") for k in keys)
    assert len(set(keys)) == 2, "each clip must follow its own track, not one shared one"
    # A track with the sound switched off would be stripped from the finished clip.
    assert all(row["keep_audio"] is True for row in rows)


@needs_ffmpeg
async def test_a_named_track_that_is_not_in_the_archive_queues_nothing(client, db):
    """Unlike a missing image: a clip rendered without its track is wasted money."""
    u = await sign_in(client)
    archive = _zip({"plan.json": json.dumps([
        {"prompt": "has one", "audio": "seg01.wav"},
        {"prompt": "does not", "audio": "seg99.wav"}]).encode(),
        "seg01.wav": _tone()})
    r = await client.post("/api/inbox/upload",
                          files={"file": ("song.zip", archive, "application/zip")})
    assert r.status_code == 400
    assert "seg99.wav" in r.json()["detail"] and "song.zip" in r.json()["detail"]
    assert await jobs.list_for(u["id"]) == []


@needs_ffmpeg
async def test_extend_cannot_be_given_a_track_from_a_batch_either(client, db):
    u = await sign_in(client)
    archive = _zip({"plan.json": json.dumps([
        {"prompt": "p", "mode": "extend", "audio": "seg01.wav"}]).encode(),
        "seg01.wav": _tone()})
    r = await client.post("/api/inbox/upload",
                          files={"file": ("song.zip", archive, "application/zip")})
    assert r.status_code == 400 and "extend" in r.json()["detail"]
    assert await jobs.list_for(u["id"]) == []


@needs_ffmpeg
async def test_audio_nobody_names_is_stored_and_changes_no_job(client, db):
    u = await sign_in(client)
    archive = _zip({"b.txt": b"a plain prompt\n", "spare.wav": _tone()})
    r = await client.post("/api/inbox/upload",
                          files={"file": ("b.zip", archive, "application/zip")})
    assert r.status_code == 200 and r.json()["queued"] == 1
    assert set(r.json()["audios"]) == {"spare.wav"}
    assert (await jobs.list_for(u["id"]))[0]["audio_key"] is None


async def test_a_plain_batch_still_names_no_track(client, db):
    u = await sign_in(client)
    await client.post("/api/inbox/upload",
                      files={"file": ("b.txt", b"one\n", "text/plain")})
    row = (await jobs.list_for(u["id"]))[0]
    assert row["audio_key"] is None and row["keep_audio"] is None


@needs_ffmpeg
async def test_a_batch_takes_a_zip_of_forty_clips_with_their_tracks(client, db):
    """The shape this was added for: a whole song, one upload."""
    u = await sign_in(client)
    entries = {f"seg{n:02d}.wav": _tone(1) for n in range(1, 41)}
    entries["plan.json"] = json.dumps({
        "defaults": {"seconds": 12, "preset": "balanced"},
        "jobs": [{"prompt": f"line {n}", "audio": f"seg{n:02d}.wav"}
                 for n in range(1, 41)]}).encode()
    r = await client.post("/api/inbox/upload",
                          files={"file": ("song.zip", _zip(entries), "application/zip")})
    assert r.status_code == 200 and r.json()["queued"] == 40
    rows = await jobs.list_for(u["id"], 100)
    assert len({row["audio_key"] for row in rows}) == 40
    assert {row["seconds"] for row in rows} == {12}
