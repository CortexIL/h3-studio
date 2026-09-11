from __future__ import annotations

import inspect

from app.backends.comfy import ComfyClient
from app.backends.mock import MockBackend
from app.backends.runpod_pod import RunpodBackend


def test_every_backend_takes_bytes():
    """Reference images live in S3 now; nothing may assume a local path."""
    for cls in (MockBackend, RunpodBackend, ComfyClient):
        params = list(inspect.signature(cls.upload_image).parameters)
        assert params[:3] == ["self", "data", "name"], cls.__name__


async def test_mock_returns_the_name_it_was_given():
    m = MockBackend(cfg=None)
    assert await m.upload_image(b"\x89PNG", "ref.png") == "ref.png"
