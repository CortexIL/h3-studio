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
from typing import Any, Iterator

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .settings import get_settings

log = logging.getLogger("h3studio.storage")

CHUNK = 1024 * 256
_RANGE_RE = re.compile(r"^bytes=\d*-\d*$")


class ObjectMissing(KeyError):
    pass


def video_key(user_id: str, job_id: str, slug: str) -> str:
    return f"videos/{user_id}/{job_id}_{slug}.mp4"


def upload_key(user_id: str, ext: str) -> str:
    if not ext.startswith("."):
        ext = "." + ext
    return f"uploads/{user_id}/{uuid.uuid4().hex[:16]}{ext}"


class Storage:
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


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    s = get_settings()
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
    return Storage(client, s.s3_bucket)
