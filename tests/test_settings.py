from __future__ import annotations

import pytest

from app.settings import Settings


def _minimal(**over):
    base = dict(
        DATABASE_URL="postgresql://u:p@h/db",
        SESSION_SECRET="x" * 32,
        S3_ENDPOINT="http://minio:9000",
        S3_BUCKET="h3",
        S3_ACCESS_KEY="ak",
        S3_SECRET_KEY="sk",
    )
    base.update(over)
    return base


def test_loads_from_environment(monkeypatch):
    for k, v in _minimal().items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.database_url == "postgresql://u:p@h/db"
    assert s.s3_bucket == "h3"
    assert s.pod_policy == "off"          # money-safe default
    assert s.mock is False


def test_missing_required_names_the_variable(monkeypatch):
    env = _minimal()
    env.pop("SESSION_SECRET")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError) as exc:
        Settings()
    assert "session_secret" in str(exc.value).lower()


def test_short_session_secret_is_rejected(monkeypatch):
    for k, v in _minimal(SESSION_SECRET="tooshort").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError):
        Settings()


def test_pod_policy_must_be_known(monkeypatch):
    for k, v in _minimal(POD_POLICY="sometimes").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError):
        Settings()


def test_database_url_must_be_postgres(monkeypatch):
    for k, v in _minimal(DATABASE_URL="sqlite:///data/h3.db").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError):
        Settings()
