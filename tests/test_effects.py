"""Effect presets travel with the clip, by name, and only the known ones."""
from app import effects
from app.store import jobs

from .conftest import sign_in


def test_clean_keeps_known_names_in_order_without_repeats_up_to_three():
    assert effects.clean(["storm_magic", "nope", "bullet_time", "storm_magic", "dark_magic", "fire_breath"]) == [
        "storm_magic", "bullet_time", "dark_magic"]
    assert effects.clean(None) == []
    assert effects.prompt_token("bullet_time") == "embedding:minimaxh3_bullet_time"


async def test_effects_are_stored_and_returned_and_copied_by_use_again(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "mode": "t2v", "effects": ["bullet_time", "nope"]})
    assert r.status_code == 200, r.text
    row = (await jobs.list_for(u["id"]))[0]
    assert row["effects"] == ["bullet_time"]
    listed = (await client.get("/api/jobs")).json()["jobs"][0]
    assert listed["effects"] == ["bullet_time"]
    await jobs.update(row["id"], status="done")
    r = await client.post(f"/api/jobs/{row['id']}/again")
    assert r.status_code == 200, r.text
    copy = await jobs.get_any(r.json()["job_id"])
    assert copy["effects"] == ["bullet_time"]


def test_every_effect_file_is_in_the_manifest():
    from app.config import Config
    manifest = {f.src.rsplit("/", 1)[-1] for f in Config().weights.files}
    for name in effects.EFFECTS:
        assert effects.embedding_file(name) in manifest, name
