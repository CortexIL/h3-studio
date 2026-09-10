"""The filesystem backend, held to the same contract as the S3 one."""
from __future__ import annotations

import pytest

from app.storage import LocalStorage, ObjectMissing

BODY = bytes(range(256)) * 4       # 1024 bytes


@pytest.fixture
def store(tmp_path):
    return LocalStorage(tmp_path / "objects")


async def test_put_then_get(store):
    await store.ensure_bucket()
    assert await store.put("videos/u1/j1.mp4", b"hello", "video/mp4") == 5
    assert await store.get("videos/u1/j1.mp4") == b"hello"


async def test_nested_keys_create_their_directories(store):
    await store.put("a/b/c/d.mp4", b"x", "video/mp4")
    assert await store.get("a/b/c/d.mp4") == b"x"


async def test_get_missing_raises_object_missing(store):
    with pytest.raises(ObjectMissing):
        await store.get("videos/u1/nope.mp4")


async def test_head(store):
    await store.put("k.mp4", BODY, "video/mp4")
    meta = await store.head("k.mp4")
    assert meta["size"] == len(BODY) and meta["content_type"] == "video/mp4"
    assert await store.head("gone.mp4") is None


async def test_stream_whole_object(store):
    await store.put("k", BODY, "video/mp4")
    chunks, size, content_range = await store.stream("k", None)
    assert b"".join(chunks) == BODY
    assert size == len(BODY) and content_range is None


async def test_stream_honours_a_range(store):
    await store.put("k", BODY, "video/mp4")
    chunks, size, content_range = await store.stream("k", "bytes=10-19")
    assert b"".join(chunks) == BODY[10:20]
    assert size == 10
    assert content_range == f"bytes 10-19/{len(BODY)}"


async def test_an_open_ended_range_runs_to_the_end(store):
    await store.put("k", BODY, "video/mp4")
    chunks, size, content_range = await store.stream("k", "bytes=1000-")
    assert b"".join(chunks) == BODY[1000:]
    assert content_range == f"bytes 1000-{len(BODY) - 1}/{len(BODY)}"


async def test_a_suffix_range_returns_the_tail(store):
    await store.put("k", BODY, "video/mp4")
    chunks, size, _ = await store.stream("k", "bytes=-16")
    assert b"".join(chunks) == BODY[-16:] and size == 16


async def test_a_range_past_the_end_is_clamped_not_fatal(store):
    await store.put("k", b"short", "video/mp4")
    chunks, size, _ = await store.stream("k", "bytes=99-200")
    assert size >= 1 and b"".join(chunks)


async def test_a_malformed_range_serves_the_whole_object(store):
    await store.put("k", BODY, "video/mp4")
    chunks, size, content_range = await store.stream("k", "bytes=nonsense")
    assert size == len(BODY) and content_range is None


async def test_streaming_something_missing_raises(store):
    with pytest.raises(ObjectMissing):
        await store.stream("nope", None)


async def test_delete(store):
    await store.put("k", b"x", "video/mp4")
    await store.delete("k")
    assert await store.head("k") is None
    await store.delete("k")            # deleting twice is not an error


async def test_a_key_cannot_escape_the_root(store):
    """Keys reach this class from a row that a browser's input helped write."""
    await store.ensure_bucket()
    with pytest.raises(ObjectMissing):
        await store.get("../../etc/passwd")
    with pytest.raises(ObjectMissing):
        await store.put("../escaped.mp4", b"x", "video/mp4")
