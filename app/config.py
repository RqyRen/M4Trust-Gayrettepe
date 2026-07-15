"""Environment-based configuration (ADR-007 §18, §41).

Ayarlar environment'tan okunur; host/port kod içine hard-code edilmez.
Local kolaylik icin guvenli default'lar bulunur, production degerleri
environment uzerinden verilir.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Runtime
    app_env: str = "local"
    log_level: str = "INFO"
    ai_api_port: int = 8000

    # Servis kimligi (capabilities/contracts endpoint'lerinde kullanilir)
    service_name: str = "ai-service"
    service_version: str = "0.1.0"

    # Desteklenen contract major/schema version'lari (ADR-002 §15, §21.3)
    supported_schema_versions: tuple[str, ...] = ("1.0.0",)


@lru_cache
def get_settings() -> Settings:
    return Settings()
