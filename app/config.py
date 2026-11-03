from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="XY_", env_file=".env", extra="ignore")

    data_dir: str = ""
    host: str = "127.0.0.1"
    port: int = 8089
    secret_key: str = ""
    log_level: str = "INFO"

    ws_url: str = "wss://wss-goofish.dingtalk.com/"
    heartbeat_interval: float = 15.0
    http_timeout: float = 30.0
    send_min_interval: float = 2.0
    account_concurrency: int = 10
    verification_mode: Literal["manual", "auto_fallback"] = "manual"
    secure_cookies: bool = False

    @property
    def data_path(self) -> Path:
        from app.platform import make_paths

        return make_paths(self.data_dir).data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
