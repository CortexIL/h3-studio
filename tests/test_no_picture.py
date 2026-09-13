"""A Reference job with no picture is a Text-to-video job.

One line of a dropped-in .txt batch became a 10-second i2v job with no image;
rendered as i2v the graph carried the template's placeholder "example.png" to
the GPU, which refused it - three times, at a rented card's prices. The same
job can come out of the composer: Reference is the default mode and nothing
stopped Create without a picture.
"""
from __future__ import annotations

from app.config import Config
from app.modes import without_picture
from app.store import jobs
from app.workflows import build_workflow
from tests.conftest import sign_in


def test_the_rule():
    assert without_picture("i2v", []) == "t2v"
    assert without_picture("i2v", ["uploads/u/a.png"]) == "i2v"
    assert without_picture("t2v", []) == "t2v"
    assert without_picture("r2v", []) == "r2v"


async def test_the_composer_path(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "a lantern rises", "mode": "i2v",
                                             "seconds": 4})
    assert r.status_code == 200, r.text
    job = (await client.get(f"/api/jobs/{r.json()['created'][0]}")).json()
    assert job["mode"] == "t2v"


async def test_the_batch_path(client, db):
    u = await sign_in(client)
    r = await client.post("/api/inbox/upload",
                          files={"file": ("lines.txt", b"a lantern rises\n", "text/plain")})
    assert r.status_code == 200, r.text
    rows = await jobs.list_for(u["id"])
    assert [row["mode"] for row in rows] == ["t2v"]


def test_the_graph_never_carries_the_placeholder():
    """Rows written before the rule existed still have to render."""
    graph = build_workflow({"mode": "i2v", "prompt": "p", "seconds": 4, "ref_images": []},
                           Config())
    assert not any(isinstance(n, dict) and n.get("class_type") == "LoadImage"
                   for n in graph.values())
    h3 = next(n for n in graph.values()
              if isinstance(n, dict) and n.get("class_type") == "MiniMaxH3ImageToVideo")
    assert "first_frame" not in h3["inputs"]
