from __future__ import annotations

import pytest

from app import storage


async def test_put_then_get(s3):
    n = await s3.put("videos/u1/j1.mp4", b"hello", "video/mp4")
    assert n == 5
    assert await s3.get("videos/u1/j1.mp4") == b"hello"


async def test_get_missing_raises_object_missing(s3):
    with pytest.raises(storage.ObjectMissing):
        await s3.get("videos/u1/nope.mp4")


async def test_head_reports_size_and_type(s3):
    await s3.put("videos/u1/j1.mp4", b"x" * 100, "video/mp4")
    meta = await s3.head("videos/u1/j1.mp4")
    assert meta["size"] == 100 and meta["content_type"] == "video/mp4"
    assert await s3.head("videos/u1/nope.mp4") is None


async def test_stream_whole_object(s3):
    await s3.put("k", b"abcdefghij", "video/mp4")
    chunks, size, content_range = await s3.stream("k", None)
    assert b"".join(chunks) == b"abcdefghij"
    assert size == 10 and content_range is None


async def test_stream_honours_a_range_header(s3):
    await s3.put("k", b"abcdefghij", "video/mp4")
    chunks, size, content_range = await s3.stream("k", "bytes=2-5")
    assert b"".join(chunks) == b"cdef"
    assert size == 4
    assert content_range == "bytes 2-5/10"


async def test_a_malformed_range_is_ignored_not_fatal(s3):
    await s3.put("k", b"abcdefghij", "video/mp4")
    chunks, size, content_range = await s3.stream("k", "bytes=nonsense")
    assert size == 10 and content_range is None


async def test_stream_missing_raises_object_missing(s3):
    with pytest.raises(storage.ObjectMissing):
        await s3.stream("nope", None)


async def test_delete(s3):
    await s3.put("k", b"x", "video/mp4")
    await s3.delete("k")
    assert await s3.head("k") is None


def test_keys_are_namespaced_by_user():
    assert storage.video_key("u1", "j1", "a-clip") == "videos/u1/j1_a-clip.mp4"
    key = storage.upload_key("u1", ".png")
    assert key.startswith("uploads/u1/") and key.endswith(".png")
    assert storage.upload_key("u1", "png").endswith(".png")
