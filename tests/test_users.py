from __future__ import annotations

import time

import pytest

from app.store import users


async def test_create_and_fetch(db):
    u = await users.create("Ann@Example.COM", "correct horse battery")
    assert u["email"] == "ann@example.com"       # normalised
    assert u["role"] == "user"
    assert "password_hash" not in u
    assert (await users.by_email("ANN@example.com"))["id"] == u["id"]


async def test_duplicate_email_is_refused(db):
    await users.create("a@b.c", "pw-one-long-enough")
    with pytest.raises(users.EmailTaken):
        await users.create("A@B.C", "pw-two-long-enough")


async def test_authenticate(db):
    await users.create("a@b.c", "s3cret-passphrase")
    assert await users.authenticate("a@b.c", "s3cret-passphrase") is not None
    assert await users.authenticate("a@b.c", "wrong") is None
    assert await users.authenticate("nobody@b.c", "s3cret-passphrase") is None


async def test_disabled_user_cannot_authenticate(db):
    u = await users.create("a@b.c", "s3cret-passphrase")
    await users.set_active(u["id"], False)
    assert await users.authenticate("a@b.c", "s3cret-passphrase") is None


async def test_disabling_bumps_token_version(db):
    u = await users.create("a@b.c", "s3cret-passphrase")
    before = (await users.by_id(u["id"]))["token_version"]
    await users.set_active(u["id"], False)
    assert (await users.by_id(u["id"]))["token_version"] == before + 1


async def test_password_change_bumps_token_version(db):
    u = await users.create("a@b.c", "s3cret-passphrase")
    before = (await users.by_id(u["id"]))["token_version"]
    await users.set_password(u["id"], "a-brand-new-passphrase")
    assert (await users.by_id(u["id"]))["token_version"] == before + 1
    assert await users.authenticate("a@b.c", "a-brand-new-passphrase") is not None


async def test_short_password_is_refused(db):
    with pytest.raises(ValueError):
        await users.create("a@b.c", "short")


async def test_bootstrap_creates_admin_only_when_empty(db):
    a = await users.ensure_bootstrap_admin("boss@h3.local", "bootstrap-passphrase")
    assert a is not None and a["role"] == "admin"
    again = await users.ensure_bootstrap_admin("other@h3.local", "another-passphrase")
    assert again is None
    assert await users.count() == 1


async def test_bootstrap_without_credentials_does_nothing(db):
    assert await users.ensure_bootstrap_admin(None, None) is None
    assert await users.count() == 0


async def test_unknown_email_costs_the_same_work_as_a_wrong_password(db):
    """A fast 'no such user' would let anyone enumerate who has an account."""
    await users.create("a@b.c", "s3cret-passphrase")

    t0 = time.perf_counter()
    await users.authenticate("a@b.c", "definitely-wrong")
    wrong_pw = time.perf_counter() - t0

    t0 = time.perf_counter()
    await users.authenticate("nobody@b.c", "definitely-wrong")
    no_user = time.perf_counter() - t0

    # Generous bound: the point is that the missing-email path still runs a
    # verification, not that the two are identical to the microsecond.
    assert no_user > wrong_pw / 3


def test_hash_round_trip():
    h = users.hash_password("hunter2-but-longer")
    assert h != "hunter2-but-longer"
    assert users.verify_password(h, "hunter2-but-longer") is True
    assert users.verify_password(h, "hunter3-but-longer") is False


def test_verify_survives_a_corrupt_hash():
    assert users.verify_password("not-a-hash", "anything") is False
