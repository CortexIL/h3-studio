"""Profile pictures: re-encoded on the server, seen only by their owner and admins.

The browser crops before uploading, but the server trusts none of it: it decodes
the file as an image, refuses anything else, and stores a fresh small WebP -
which also drops whatever a phone wrote into the original, GPS included.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app import avatars
from app import storage as storage_mod
from app.store import users
from tests.conftest import sign_in

RED, BLUE = (220, 20, 20), (20, 20, 220)


def _image(size=(800, 600), fmt="PNG", color=RED, exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    extra = {"exif": exif} if exif is not None else {}
    Image.new("RGB", size, color).save(buf, fmt, **extra)
    return buf.getvalue()


def _noise(size=(300, 300)) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise(size, 80).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _is(pixel, color) -> bool:
    return all(abs(a - b) < 40 for a, b in zip(pixel[:3], color))


# ---------------------------------------------------------------- processing

def test_process_makes_a_small_square_webp():
    img = Image.open(io.BytesIO(avatars.process(_image((800, 600)))))
    assert img.format == "WEBP"
    assert img.size == (avatars.SIZE, avatars.SIZE)


def test_process_drops_what_the_camera_wrote_into_the_file():
    exif = Image.Exif()
    exif[0x010F] = "SomeCamera"  # Make
    out = avatars.process(_image(fmt="JPEG", exif=exif.tobytes()))
    assert not Image.open(io.BytesIO(out)).getexif()


def test_process_applies_the_camera_rotation():
    # 400x200, red on the left, blue on the right; orientation 6 means "turn it
    # 90 degrees clockwise to view", which puts red on top.
    src = Image.new("RGB", (400, 200), BLUE)
    src.paste(RED, (0, 0, 200, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    src.save(buf, "JPEG", exif=exif.tobytes(), quality=95)
    img = Image.open(io.BytesIO(avatars.process(buf.getvalue()))).convert("RGB")
    assert _is(img.getpixel((avatars.SIZE // 2, 10)), RED)
    assert _is(img.getpixel((avatars.SIZE // 2, avatars.SIZE - 10)), BLUE)


@pytest.mark.parametrize("data", [
    b"",
    b"not an image at all",
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
])
def test_process_refuses_what_is_not_an_image(data):
    with pytest.raises(ValueError):
        avatars.process(data)


def test_process_refuses_an_absurd_pixel_count():
    # A tiny file that decodes to an enormous canvas.
    buf = io.BytesIO()
    Image.new("1", (12_000, 12_000)).save(buf, "PNG")
    with pytest.raises(ValueError):
        avatars.process(buf.getvalue())


# ---------------------------------------------------------------- routes

async def _upload(client, data: bytes | None = None, name="me.png", ctype="image/png"):
    return await client.post("/api/me/avatar",
                             files={"file": (name, data if data is not None else _image(), ctype)})


async def test_an_uploaded_picture_shows_in_me_and_can_be_fetched(client, db):
    u = await sign_in(client)
    r = await _upload(client)
    assert r.status_code == 200, r.text
    url = r.json()["avatar_url"]
    assert url.startswith(f"/api/avatar/{u['id']}?v=")

    me = (await client.get("/api/me")).json()
    assert me["avatar_url"] == url
    assert "avatar_key" not in me

    img = await client.get(url)
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/webp"
    # The URL changes with the picture, so it can be cached for good - privately.
    assert img.headers["cache-control"] == "private, max-age=31536000, immutable"


async def test_no_picture_is_null_and_a_404(client, db):
    u = await sign_in(client)
    assert (await client.get("/api/me")).json()["avatar_url"] is None
    assert (await client.get(f"/api/avatar/{u['id']}")).status_code == 404


async def test_replacing_or_removing_a_picture_deletes_the_old_file(client, db):
    u = await sign_in(client)
    store = storage_mod.get_storage()
    await _upload(client)
    first = (await users.by_id(u["id"]))["avatar_key"]
    await _upload(client, _image(color=BLUE))
    second = (await users.by_id(u["id"]))["avatar_key"]
    assert first != second
    assert await store.head(first) is None
    assert await store.head(second) is not None

    r = await client.delete("/api/me/avatar")
    assert r.status_code == 200 and r.json()["avatar_url"] is None
    assert await store.head(second) is None
    assert (await users.by_id(u["id"]))["avatar_key"] is None


async def test_a_file_that_is_not_an_image_is_refused(client, db):
    await sign_in(client)
    r = await _upload(client, b"GIF89a but not really", "evil.png")
    assert r.status_code == 400


async def test_a_file_over_the_size_limit_is_refused(client, db, monkeypatch):
    from app.routes import avatar as avatar_routes
    monkeypatch.setattr(avatar_routes, "MAX_AVATAR_BYTES", 10_000)
    await sign_in(client)
    r = await _upload(client, _noise())
    assert r.status_code == 413


async def test_another_user_gets_404_and_an_admin_can_see_it(client, db):
    owner = await sign_in(client, "owner@h3.local")
    url = (await _upload(client)).json()["avatar_url"]

    await client.post("/api/auth/logout")
    await sign_in(client, "other@h3.local")
    assert (await client.get(url)).status_code == 404

    await client.post("/api/auth/logout")
    await sign_in(client, "boss@h3.local", role="admin")
    assert (await client.get(url)).status_code == 200
    row = next(u for u in (await client.get("/api/admin/users")).json()["users"]
               if u["id"] == owner["id"])
    assert row["avatar_url"] == url
    assert "avatar_key" not in row


async def test_a_malformed_user_id_is_a_404_not_a_crash(client, db):
    await sign_in(client)
    assert (await client.get("/api/avatar/not-a-uuid")).status_code == 404
