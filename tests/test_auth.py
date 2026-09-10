from __future__ import annotations

import time

import pytest

from app import auth


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from app.settings import get_settings
    for k, v in {
        "DATABASE_URL": "postgresql://u:p@h/db",
        "SESSION_SECRET": "s" * 40,
        "S3_ENDPOINT": "http://minio:9000",
        "S3_BUCKET": "h3",
        "S3_ACCESS_KEY": "ak",
        "S3_SECRET_KEY": "sk",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    auth._serializer.cache_clear()
    yield
    get_settings.cache_clear()
    auth._serializer.cache_clear()


def test_token_round_trip():
    token = auth.issue({"id": "abc", "token_version": 3})
    assert auth.read(token) == ("abc", 3)


def test_tampered_token_is_rejected():
    token = auth.issue({"id": "abc", "token_version": 1})
    assert auth.read(token[:-3] + "aaa") is None


def test_garbage_token_is_rejected():
    assert auth.read("not-a-token") is None
    assert auth.read("") is None


def test_expired_token_is_rejected(monkeypatch):
    token = auth.issue({"id": "abc", "token_version": 1})
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 60 * 60 * 24 * 400)
    assert auth.read(token) is None


def test_a_token_signed_with_another_secret_is_rejected(monkeypatch):
    """A rotated SESSION_SECRET must invalidate every outstanding cookie."""
    from app.settings import get_settings
    token = auth.issue({"id": "abc", "token_version": 1})
    monkeypatch.setenv("SESSION_SECRET", "d" * 40)
    get_settings.cache_clear()
    auth._serializer.cache_clear()
    assert auth.read(token) is None


def test_a_wrong_password_streak_on_one_account_is_throttled():
    """Ten guesses at one account from one address, then stop."""
    from app.routes.auth import MAX_PER_ACCOUNT, _rate_limited, reset_rate_limits
    reset_rate_limits()
    for _ in range(MAX_PER_ACCOUNT):
        assert _rate_limited("1.2.3.4", "victim@h3.local") is False
    assert _rate_limited("1.2.3.4", "victim@h3.local") is True


def test_one_throttled_account_does_not_lock_out_the_office():
    """Colleagues share a NAT address; one of them fumbling must not stop the rest."""
    from app.routes.auth import MAX_PER_ACCOUNT, _rate_limited, reset_rate_limits
    reset_rate_limits()
    for _ in range(MAX_PER_ACCOUNT + 5):
        _rate_limited("1.2.3.4", "clumsy@h3.local")
    assert _rate_limited("1.2.3.4", "colleague@h3.local") is False


def test_spraying_many_accounts_from_one_address_still_hits_a_ceiling():
    from app.routes.auth import MAX_PER_IP, _rate_limited, reset_rate_limits
    reset_rate_limits()
    for i in range(MAX_PER_IP):
        assert _rate_limited("9.9.9.9", f"target{i}@h3.local") is False
    assert _rate_limited("9.9.9.9", "one-more@h3.local") is True
