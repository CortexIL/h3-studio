"""Every knob this process reads, and where it comes from.

Configuration is environment-only. The desktop build wrote config.yaml back to
disk from the UI; a container filesystem does not survive a redeploy, so the
settings an admin can change at runtime - RunPod key, pod policy, budget - live in
the `kv` table instead, and the values here are only their initial defaults.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, case_sensitive=False,
                                      extra="ignore")

    # --- required: the app refuses to start without these ---
    database_url: str
    # Never generated at boot: a fresh secret would sign every user out on each
    # redeploy, and two replicas would not agree on it.
    session_secret: str

    # --- object storage ---
    # 'local' keeps objects on disk under local_storage_dir. It exists so the app
    # can be run and exercised end to end without an S3 to point at; production
    # is 's3'. Both go through the same Storage interface, so the media routes do
    # not know or care which is behind them.
    storage_backend: Literal["s3", "local"] = "s3"
    local_storage_dir: str = "/data/objects"
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    # --- optional ---
    admin_email: str | None = None
    admin_password: str | None = None
    runpod_api_key: str = ""
    # 'off' rather than 'auto': auto rents a GPU the moment anyone queues, and a
    # fresh deploy should never start billing before an admin says so.
    pod_policy: Literal["auto", "keep-warm", "off"] = "off"
    budget_session_limit_usd: float = 8.0
    mock: bool = False
    # H3 emits audio with every clip. It is real at thirty steps and unusable
    # noise under the four-step turbo LoRA, which distils the video branch only -
    # so this is a per-install choice, not a default worth flipping for everyone.
    keep_audio: bool = True
    session_max_age_days: int = 14
    # Only ever false for local HTTP development; a Secure cookie is not sent
    # over plain HTTP, so login would silently never stick.
    cookie_secure: bool = True
    host: str = "0.0.0.0"
    port: int = 8777

    @field_validator("session_secret")
    @classmethod
    def _secret_long_enough(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _s3_needs_its_credentials(self) -> "Settings":
        if self.storage_backend != "s3":
            return self
        # Not s3_endpoint: an empty endpoint means real AWS, which is a valid
        # thing to point at even though this deployment uses a MinIO URL.
        missing = [n for n in ("s3_bucket", "s3_access_key", "s3_secret_key")
                   if not getattr(self, n)]
        if missing:
            raise ValueError(
                "STORAGE_BACKEND is s3, so these must be set: "
                + ", ".join(m.upper() for m in missing))
        return self

    @field_validator("database_url")
    @classmethod
    def _pg_only(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a postgresql:// URL")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
