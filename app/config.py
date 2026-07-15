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

    # RabbitMQ (ADR-002 §5) — local default'lar, prod environment'tan gelir
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_vhost: str = "/"
    worker_prefetch: int = 8

    # Kaynak indirme (ADR-001 §6, ADR-002 §7.1)
    download_timeout_seconds: float = 30.0
    download_max_bytes: int = 256 * 1024 * 1024  # 256 MiB
    download_chunk_bytes: int = 1024 * 1024

    # LLM tabanli document extraction (ADR-004 SS16 - Yusuf'un serbest model secimi)
    # Secret; .env disinda hicbir yerde literal olarak yazilmaz (ADR-007 SS19).
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4"
    openai_timeout_seconds: float = 60.0
    openai_max_source_chars: int = 60_000  # asiri uzun dokuman icin prompt sinirlamasi


@lru_cache
def get_settings() -> Settings:
    return Settings()
