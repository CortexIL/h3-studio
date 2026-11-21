from app.store import jobs, projects, users


async def _team():
    o, e, s = [(await users.create(f"{n}@h3.local", "passphrase-1"))["id"] for n in "oes"]
    p = (await projects.create(o, "School film"))["id"]
    await projects.add_member(p, e, "editor")
    return o, e, s, p


async def _done(jid: str) -> None:
    await jobs.update(jid, status="done", output_key=f"videos/x/{jid}.mp4", finished_at=1.0)


async def test_a_member_reads_a_teammates_clip_with_their_own_role(db):
    o, e, s, p = await _team()
    jid = await jobs.add(o, "a dog", project_id=p)
    row = await jobs.get_for(e, jid)
    assert (row["my_role"], row["made_by"]) == ("editor", "o@h3.local")
    assert await jobs.get_for(s, jid) is None


async def test_the_project_feed_holds_everyones_clips(db):
    o, e, s, p = await _team()
    a = await jobs.add(o, "a", project_id=p)
    b = await jobs.add(e, "b", project_id=p)
    await jobs.add(e, "elsewhere")
    assert {r["id"] for r in await jobs.list_for(e, project_id=p)} == {a, b}
    assert (await jobs.feed_counts_for(e, project_id=p))["all"] == 2
    assert set(await jobs.queue_positions_for(e, project_id=p)) == {a, b}


async def test_without_a_project_the_feed_is_still_only_mine(db):
    o, e, s, p = await _team()
    await jobs.add(o, "a", project_id=p)
    b = await jobs.add(e, "b", project_id=p)
    assert [r["id"] for r in await jobs.list_for(e)] == [b]


async def test_the_archive_can_be_one_project(db):
    o, e, s, p = await _team()
    for jid in (await jobs.add(o, "a", project_id=p), await jobs.add(e, "b", project_id=p)):
        await _done(jid)
    await _done(await jobs.add(e, "elsewhere"))
    rows, _ = await jobs.archive_page(e, None, project_id=p)
    assert len(rows) == 2
    assert await jobs.archive_count(e, project_id=p) == 2
    assert len(await jobs.archive_ids(e, project_id=p)) == 2


async def test_clearing_a_project_feed_touches_only_my_clips(db):
    o, e, s, p = await _team()
    theirs = await jobs.add(o, "a", project_id=p)
    await jobs.add(e, "b", project_id=p)
    assert await jobs.clear_queued_for(e, project_id=p) == 1
    assert await jobs.get_any(theirs) is not None


async def test_reference_keys_are_visible_to_teammates_only(db):
    o, e, s, p = await _team()
    pic, kf = f"uploads/{o}/pic.png", f"uploads/{o}/kf.png"
    await jobs.add(o, "a", project_id=p, ref_images=[pic])
    await jobs.add(o, "b", project_id=p, keyframes=[{"key": kf, "at": 2.0}])
    assert await jobs.key_visible_to(e, pic) and await jobs.key_visible_to(e, kf)
    assert not await jobs.key_visible_to(s, pic)
