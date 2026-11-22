"""The HuggingFace token: verified with HF, stored, shown by its tail, used by the next pod."""
import httpx

from app.backends.runpod_pod import _bootstrap_cmd

from .conftest import sign_in


def _hf_answers(monkeypatch, status):
    seen = []
    real_get = httpx.AsyncClient.get

    async def fake_get(self, url, **kw):
        if not str(url).startswith("https://huggingface.co/"):
            return await real_get(self, url, **kw)  # the test client itself
        seen.append((url, kw["headers"]["Authorization"]))
        return httpx.Response(status)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return seen


async def test_the_token_is_verified_stored_and_reaches_the_pod(client, db, monkeypatch):
    await sign_in(client, email="admin@h3.local", role="admin")
    seen = _hf_answers(monkeypatch, 200)
    r = await client.post("/api/admin/hf-key", json={"key": "  hf_abcd9876  "})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "hint": "9876"}
    assert seen == [("https://huggingface.co/api/whoami-v2", "Bearer hf_abcd9876")]
    assert (await client.get("/api/admin/hf-key-state")).json() == {"present": True, "hint": "9876"}
    cfg = client._transport.app.state.cfg  # noqa: SLF001 - the ASGI app behind the test client
    assert "export HF_TOKEN=hf_abcd9876" in _bootstrap_cmd(cfg)[2]


async def test_a_rejected_token_is_not_stored(client, db, monkeypatch):
    await sign_in(client, email="admin@h3.local", role="admin")
    _hf_answers(monkeypatch, 401)
    r = await client.post("/api/admin/hf-key", json={"key": "hf_bad"})
    assert r.status_code == 400
    assert (await client.get("/api/admin/hf-key-state")).json()["present"] is False
