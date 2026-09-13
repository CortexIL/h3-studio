"""The prompt helper: gated on a key, verified before it is stored, rewrites the three fields."""
import json

from app import prompting

from .conftest import sign_in


async def test_without_a_key_the_helper_says_so(client, db):
    await sign_in(client)
    r = await client.post("/api/prompt/improve", json={"prompt": "a lantern on a wall"})
    assert r.status_code == 409
    assert "Admin page" in r.json()["detail"]


async def test_the_helper_returns_the_three_fields(client, db, monkeypatch):
    await sign_in(client)
    client._transport.app.state.cfg.anthropic_api_key = "sk-test-1234"  # noqa: SLF001 - the ASGI app behind the test client
    seen = {}

    async def fake_ask(api_key, system, user, **kw):
        seen["key"] = api_key
        seen["system"] = system
        seen["user"] = json.loads(user)
        return json.dumps({"description": "[Shot 1] Live-action, a lantern sways on a wall.",
                           "sounds": "Wind moves through leaves.", "music": ""})

    monkeypatch.setattr(prompting, "ask", fake_ask)
    r = await client.post("/api/prompt/improve", json={
        "prompt": "a lantern on a wall", "mode": "i2v", "seconds": 8, "has_start": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"description": "[Shot 1] Live-action, a lantern sways on a wall.",
                        "sounds": "Wind moves through leaves.", "music": ""}
    assert seen["key"] == "sk-test-1234"
    assert "integrated_multimodal_description" in seen["system"]   # the official guide rides along
    assert seen["user"]["task"].startswith("I2VA")
    assert seen["user"]["duration_seconds"] == 8


async def test_a_broken_answer_is_a_502_not_a_crash(client, db, monkeypatch):
    await sign_in(client)
    client._transport.app.state.cfg.anthropic_api_key = "sk-test-1234"  # noqa: SLF001 - the ASGI app behind the test client

    async def fake_ask(api_key, system, user, **kw):
        return "sorry, no"

    monkeypatch.setattr(prompting, "ask", fake_ask)
    r = await client.post("/api/prompt/improve", json={"prompt": "a lantern on a wall"})
    assert r.status_code == 502


def test_parse_tolerates_a_code_fence():
    out = prompting.parse('```json\n{"description": "[Shot 1] x", "sounds": "", "music": "piano"}\n```')
    assert out == {"description": "[Shot 1] x", "sounds": "", "music": "piano"}


async def test_the_admin_key_is_verified_stored_and_shown_by_its_tail(client, db, monkeypatch):
    await sign_in(client, email="admin@h3.local", role="admin")
    calls = []

    async def fake_verify(key):
        calls.append(key)

    monkeypatch.setattr(prompting, "verify_key", fake_verify)
    r = await client.post("/api/admin/prompt-key", json={"key": "  sk-ant-abcd9876  "})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "hint": "9876"}
    assert calls == ["sk-ant-abcd9876"]
    state = (await client.get("/api/admin/prompt-key-state")).json()
    assert state == {"present": True, "hint": "9876"}
    status = (await client.get("/api/status")).json()
    assert status["config"]["prompt_helper"] is True


async def test_a_rejected_key_is_not_stored(client, db, monkeypatch):
    await sign_in(client, email="admin@h3.local", role="admin")

    async def fake_verify(key):
        raise prompting.PromptHelperError("the prompt helper rejected that key")

    monkeypatch.setattr(prompting, "verify_key", fake_verify)
    r = await client.post("/api/admin/prompt-key", json={"key": "bad"})
    assert r.status_code == 400
    assert (await client.get("/api/admin/prompt-key-state")).json()["present"] is False
