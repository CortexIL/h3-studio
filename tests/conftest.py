"""Fixtures shared by the whole suite.

The database is real Postgres rather than a stub: the code under test relies on
FOR UPDATE SKIP LOCKED, advisory locks and JSONB, none of which a fake reproduces,
and getting those wrong is exactly the class of bug worth catching.
"""
from __future__ import annotations

import getpass
import os

import psycopg
import pytest
import pytest_asyncio

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql://{getpass.getuser()}@localhost:5432/h3_test",
)


def _reachable() -> bool:
    try:
        with psycopg.connect(TEST_DSN, connect_timeout=2):
            return True
    except psycopg.Error:
        return False


def pytest_collection_modifyitems(config, items):
    if _reachable():
        return
    skip = pytest.mark.skip(
        reason=f"No Postgres at {TEST_DSN}. Create one:\n"
               "  createdb h3_test   (or set TEST_DATABASE_URL)"
    )
    for item in items:
        if "db" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def dsn() -> str:
    return TEST_DSN


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """The login buckets are process-global, so tests would throttle each other."""
    from app.routes.auth import reset_rate_limits
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest_asyncio.fixture
async def db(dsn):
    """A clean, migrated schema per test."""
    from app.store import pool as pool_mod

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await pool_mod.close_pool()
    await pool_mod.open_pool(dsn)
    async with pool_mod.connection() as conn:
        await pool_mod.migrate(conn)
    yield pool_mod
    await pool_mod.close_pool()


@pytest.fixture
def s3(monkeypatch):
    """An in-memory S3, via moto, wired into app.storage."""
    import boto3
    from moto import mock_aws

    from app import storage
    from app.settings import get_settings

    for k, v in {
        "DATABASE_URL": "postgresql://u:p@h/db",
        "SESSION_SECRET": "s" * 40,
        "S3_ENDPOINT": "",              # moto intercepts the real endpoint
        "S3_BUCKET": "h3-test",
        "S3_ACCESS_KEY": "testing",
        "S3_SECRET_KEY": "testing",
        "S3_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    storage.get_storage.cache_clear()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="h3-test")
        yield storage.get_storage()
    get_settings.cache_clear()
    storage.get_storage.cache_clear()


@pytest_asyncio.fixture
async def app_settings(monkeypatch, dsn, tmp_path):
    """Environment for an app instance wired to the test database and no GPU."""
    from app.settings import get_settings
    for k, v in {
        "DATABASE_URL": dsn,
        "SESSION_SECRET": "t" * 40,
        "S3_ENDPOINT": "",           # moto intercepts the real endpoint
        "S3_BUCKET": "h3-test",
        "S3_ACCESS_KEY": "ak",
        "S3_SECRET_KEY": "sk",
        "MOCK": "true",
        "COOKIE_SECURE": "false",
        "POD_POLICY": "off",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def spa_dist(tmp_path, monkeypatch):
    """A stand-in for frontend/dist, so no test needs Node.

    Building the client is the Dockerfile's job. What the server does with the
    build - which routes get index.html, with what title and headers - is
    tested against this.
    """
    import app.main as main_mod
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head><title>H3 Studio</title>'
        '<script type="module" src="/assets/app-3f9a1c.js"></script></head>'
        '<body><div id="root"></div></body></html>', encoding="utf-8")
    (dist / "assets" / "app-3f9a1c.js").write_text("console.log('h3')", encoding="utf-8")
    monkeypatch.setattr(main_mod, "DIST", dist)
    return dist


@pytest_asyncio.fixture
async def client(db, app_settings, spa_dist):
    """An httpx client bound to the real ASGI app, cookies included.

    COOKIE_SECURE is false in app_settings for a reason: httpx will not store a
    Secure cookie sent over http://test, so every signed-in test would silently
    be anonymous.

    The object store is moto rather than a stub, because uploads go through the
    app's own storage client - pointing it at an unreachable endpoint would make
    every upload test a slow connection-refused instead of a real round trip.
    """
    import boto3
    import httpx
    from moto import mock_aws

    from app import storage
    from app.main import create_app

    storage.get_storage.cache_clear()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(
            Bucket=app_settings.s3_bucket)
        app = create_app(app_settings)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            async with app.router.lifespan_context(app):
                yield c
    storage.get_storage.cache_clear()


async def sign_in(client, email="a@h3.local", password="passphrase-1",
                  role="user"):
    """Create a user and log the client in as them."""
    from app.store import users
    u = await users.create(email, password, role=role)
    r = await client.post("/api/auth/login",
                          json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return u
