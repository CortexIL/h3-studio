"""The ComfyUI calls a cancel and a lost-render decision rest on, against the
wire shapes ComfyUI actually uses."""
from __future__ import annotations

import json

import httpx
import pytest

from app.backends.comfy import ComfyClient, ComfyError


def _client(handler) -> ComfyClient:
    c = ComfyClient("http://comfy.test")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


# ComfyUI's queue items are positional: [number, prompt_id, prompt, extra_data, outputs]
QUEUE = {
    "queue_running": [[3, "run-1", {}, {}, ["9"]]],
    "queue_pending": [[4, "wait-1", {}, {}, ["9"]], [5, "wait-2", {}, {}, ["9"]]],
}


async def test_queued_ids_reads_the_prompt_id_out_of_each_positional_item():
    c = _client(lambda req: httpx.Response(200, json=QUEUE))
    assert await c.queued_ids() == ({"run-1"}, {"wait-1", "wait-2"})


async def test_queued_ids_raises_when_the_pod_does_not_answer():
    """queue_state() swallows errors for a status line. A cancel or a lost-render
    decision must never be made on a swallowed error."""
    c = _client(lambda req: httpx.Response(502))
    with pytest.raises(httpx.HTTPError):
        await c.queued_ids()


async def test_delete_queued_posts_the_delete_list():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(200)

    await _client(handler).delete_queued("wait-1")
    assert seen == [("POST", "/queue", {"delete": ["wait-1"]})]


async def test_interrupt_posts_to_interrupt():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        return httpx.Response(200)

    await _client(handler).interrupt()
    assert seen == [("POST", "/interrupt")]


async def test_a_history_the_pod_cannot_serve_is_an_error_not_an_absence():
    """ComfyUI answers an unknown id with an empty 200; a 503 is the proxy, and
    treating it as 'no record' would requeue a render that is going fine."""
    c = _client(lambda req: httpx.Response(503))
    with pytest.raises(ComfyError):
        await c.history("p1")


async def test_an_unknown_prompt_has_no_history():
    c = _client(lambda req: httpx.Response(200, json={}))
    assert await c.history("p1") is None
