"""The object store: finished clips and uploaded reference images.

boto3 is synchronous, so every call is pushed to a thread. The alternative - an
async S3 client - would add a dependency to save a thread hop on a path that is
already dominated by moving tens of megabytes over a socket.

The bucket is private. Nothing here ever mints a public or presigned URL; the app
reads objects and streams them to the browser after checking the database says
that user owns the job. Keys carry the user id for legibility, never for
authorization.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .settings import get_settings

log = logging.getLogger("h3studio.storage")

CHUNK = 1024 * 256
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class ObjectMissing(KeyError):
    pass


def video_key(user_id: str, job_id: str, slug: str) -> str:
    return f"videos/{user_id}/{job_id}_{slug}.mp4"


def poster_key(user_id: str, job_id: str) -> str:
    return f"posters/{user_id}/{job_id}.jpg"


def avatar_key(user_id: str) -> str:
    # A new name for every picture, so its URL can be cached for good.
    return f"avatars/{user_id}/{uuid.uuid4().hex[:16]}.webp"


def upload_key(user_id: str, ext: str) -> str:
    if not ext.startswith("."):
        ext = "." + ext
    return f"uploads/{user_id}/{uuid.uuid4().hex[:16]}{ext}"


class S3Storage:
    def __init__(self, client: Any, bucket: str) -> None:
        self._c = client
        self.bucket = bucket

    async def ensure_bucket(self) -> None:
        def _go() -> None:
            try:
                self._c.head_bucket(Bucket=self.bucket)
            except ClientError:
                log.info("creating bucket %s", self.bucket)
                self._c.create_bucket(Bucket=self.bucket)
        await asyncio.to_thread(_go)

    async def put(self, key: str, data: bytes, content_type: str) -> int:
        await asyncio.to_thread(
            self._c.put_object, Bucket=self.bucket, Key=key, Body=data,
            ContentType=content_type)
        return len(data)

    async def get(self, key: str) -> bytes:
        def _go() -> bytes:
            try:
                return self._c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except ClientError as e:
                raise ObjectMissing(key) from e
        return await asyncio.to_thread(_go)

    async def head(self, key: str) -> dict[str, Any] | None:
        def _go() -> dict[str, Any] | None:
            try:
                r = self._c.head_object(Bucket=self.bucket, Key=key)
            except ClientError:
                return None
            return {"size": int(r["ContentLength"]),
                    "content_type": r.get("ContentType", "application/octet-stream")}
        return await asyncio.to_thread(_go)

    async def stream(self, key: str, byte_range: str | None
                     ) -> tuple[Iterator[bytes], int, str | None]:
        """Return an iterator of chunks, the byte count, and a Content-Range.

        Range support is not decoration: without it a browser cannot seek in an
        MP4 served from here, so scrubbing a ten-second clip means downloading it
        again from the start. A range header that does not parse is dropped rather
        than passed through, since S3 would reject it and the user would see a
        broken player instead of a clip that merely starts at the beginning.
        """
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if byte_range and _RANGE_RE.match(byte_range):
            kwargs["Range"] = byte_range

        def _go():
            try:
                return self._c.get_object(**kwargs)
            except ClientError as e:
                raise ObjectMissing(key) from e

        r = await asyncio.to_thread(_go)
        body = r["Body"]
        size = int(r["ContentLength"])
        content_range = r.get("ContentRange")

        def _chunks() -> Iterator[bytes]:
            try:
                while chunk := body.read(CHUNK):
                    yield chunk
            finally:
                body.close()

        return _chunks(), size, content_range

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._c.delete_object, Bucket=self.bucket, Key=key)


class LocalStorage:
    """The same interface, backed by a directory.

    Here so the app can be run and exercised end to end without an S3 to point
    at. Keys are used as relative paths, and every one is re-anchored under the
    root before it is touched - a key reaches this class from a database row, but
    the row was written from something a browser sent.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise ObjectMissing(key)
        return target

    async def ensure_bucket(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes, content_type: str) -> int:
        def _go() -> int:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return len(data)
        return await asyncio.to_thread(_go)

    async def get(self, key: str) -> bytes:
        def _go() -> bytes:
            try:
                return self._path(key).read_bytes()
            except OSError as e:
                raise ObjectMissing(key) from e
        return await asyncio.to_thread(_go)

    async def head(self, key: str) -> dict[str, Any] | None:
        def _go() -> dict[str, Any] | None:
            try:
                path = self._path(key)
                if not path.is_file():
                    return None
                return {"size": path.stat().st_size,
                        "content_type": "video/mp4" if path.suffix == ".mp4"
                        else "application/octet-stream"}
            except ObjectMissing:
                return None
        return await asyncio.to_thread(_go)

    async def stream(self, key: str, byte_range: str | None
                     ) -> tuple[Iterator[bytes], int, str | None]:
        def _open():
            path = self._path(key)
            if not path.is_file():
                raise ObjectMissing(key)
            return path, path.stat().st_size

        path, total = await asyncio.to_thread(_open)
        start, end = 0, total - 1
        if byte_range and (m := _RANGE_RE.match(byte_range)):
            lo, hi = m.group(1), m.group(2)
            if lo:
                start = min(int(lo), max(total - 1, 0))
                end = min(int(hi), total - 1) if hi else total - 1
            elif hi:                       # bytes=-500 means the last 500 bytes
                start = max(total - int(hi), 0)
            if end < start:
                start, end = 0, total - 1
        else:
            byte_range = None

        size = end - start + 1
        content_range = f"bytes {start}-{end}/{total}" if byte_range else None

        def _chunks() -> Iterator[bytes]:
            remaining = size
            with path.open("rb") as fh:
                fh.seek(start)
                while remaining > 0 and (chunk := fh.read(min(CHUNK, remaining))):
                    remaining -= len(chunk)
                    yield chunk

        return _chunks(), size, content_range

    async def delete(self, key: str) -> None:
        def _go() -> None:
            try:
                self._path(key).unlink(missing_ok=True)
            except ObjectMissing:
                pass
        await asyncio.to_thread(_go)


@lru_cache(maxsize=1)
def get_storage():
    s = get_settings()
    if s.storage_backend == "local":
        return LocalStorage(s.local_storage_dir)
    client = boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint or None,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
        # MinIO and most self-hosted S3 do not resolve virtual-host style
        # bucket.endpoint names; path style works on both.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return S3Storage(client, s.s3_bucket)
