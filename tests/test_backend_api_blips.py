"""A status poll the RunPod API fails to answer is not a dead pod.

Every boot on the production box produced "pod start failed: All connection
attempts failed": the container resolves rest.runpod.io to IPv6 addresses it
cannot route to, and a poll whose IPv4 attempt also slipped raised from
status(), which reported the pod as an error, which ensure_ready() treated as
the pod dying. The pod was fine and downloading weights the whole time.
"""
from __future__ import annotations

import time

import httpx

from app.backends.comfy import IPV4_LOCAL_ADDRESS, ComfyClient
from app.backends.runpod_pod import API_BLIPS_TOLERATED, RunpodBackend, RunpodError, make_http
from app.config import Config


def _backend() -> RunpodBackend:
    b = RunpodBackend(Config())
    b._pod_id = "p-booting"
    b._started_at = time.time()

    async def no_network() -> str:
        return "downloading 2/19"

    # A RUNNING answer goes on to ask the pod's bootstrap what it is doing; keep
    # that off the network.
    b._bootstrap_progress = no_network          # type: ignore[method-assign]
    return b


def _blips(backend: RunpodBackend, replies: list[object]) -> None:
    async def fake_api(method: str, path: str, **kw):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    backend._api = fake_api          # type: ignore[method-assign]


def _lost() -> httpx.ConnectError:
    return httpx.ConnectError("All connection attempts failed")


def _gateway() -> RunpodError:
    err = RunpodError("RunPod GET /pods/p-booting -> 502: bad gateway")
    err.status = 502
    return err


class _Comfy:
    def __init__(self, alive: bool) -> None:
        self.alive = alive

    async def is_alive(self) -> bool:
        return self.alive

    async def aclose(self) -> None:
        pass


def test_the_runpod_client_only_uses_ipv4_and_reconnects_once():
    pool = make_http("k")._transport._pool          # type: ignore[union-attr]
    assert pool._local_address == IPV4_LOCAL_ADDRESS
    assert pool._retries == 1


def test_the_comfy_client_only_uses_ipv4_too():
    pool = ComfyClient("https://p-8188.proxy.runpod.net")._http._transport._pool  # type: ignore[union-attr]
    assert pool._local_address == IPV4_LOCAL_ADDRESS
    assert pool._retries == 1


async def test_a_lost_connection_while_booting_is_still_booting():
    b = _backend()
    _blips(b, [_lost()])
    st = await b.status()
    assert st.state == "booting"
    assert st.pod_id == "p-booting"
    assert "RunPod API unreachable" in st.detail


async def test_a_lost_connection_while_serving_is_still_ready():
    """ComfyUI answering is the proof that matters: the pod is up whatever the API says."""
    b = _backend()
    b._comfy = _Comfy(alive=True)   # type: ignore[assignment]
    _blips(b, [_lost()])
    st = await b.status()
    assert st.state == "ready"
    assert st.endpoint == "https://p-booting-8188.proxy.runpod.net"


async def test_a_5xx_from_the_api_is_a_blip_not_a_verdict():
    b = _backend()
    _blips(b, [_gateway()])
    assert (await b.status()).state == "booting"


async def test_a_4xx_other_than_404_is_still_an_error():
    b = _backend()
    err = RunpodError("RunPod GET /pods/p-booting -> 401: unauthorized")
    err.status = 401
    _blips(b, [err])
    assert (await b.status()).state == "error"


async def test_a_run_of_blips_becomes_an_error():
    b = _backend()
    _blips(b, [_lost() for _ in range(API_BLIPS_TOLERATED)])
    for _ in range(API_BLIPS_TOLERATED - 1):
        assert (await b.status()).state == "booting"
    final = await b.status()
    assert final.state == "error"
    assert "polls in a row" in final.detail


async def test_an_answer_resets_the_blip_count():
    b = _backend()
    replies: list[object] = []
    for _ in range(3):
        replies += [_lost(), _lost(), {"desiredStatus": "RUNNING"}]
    _blips(b, replies)
    # Two blips, an answer, repeated: never three in a row, never an error.
    for _ in range(9):
        assert (await b.status()).state == "booting"
