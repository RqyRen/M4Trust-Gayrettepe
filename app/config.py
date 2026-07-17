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
    worker_health_port: int = 8001

    # Servis kimligi (capabilities/contracts endpoint'lerinde kullanilir)
    # Kanonik ad ADR-007 SS9 / contracts CHANGELOG'da m4trust-ai-service olarak sabit.
    service_name: str = "m4trust-ai-service"
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

    # Idempotency job store (ADR-002 §17.1) — Redis: coklu worker replica'si
    # arasinda paylasilan, kalici job durumu. In-memory tek worker'da yeterliydi
    # ama replica sayisi 1'i gectiginde her worker kendi hafizasinda ayri
    # kayit tutuyordu ve duplicate/terminal tespiti kirilyordu.
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    # Terminal (completed/failed) kayitlarin ne kadar sure tutulacagi; bu sure
    # icinde gelen duplicate teslimatlar onceki sonucu yeniden yayinlar.
    idempotency_ttl_seconds: int = 259_200  # 72 saat
    # Bir worker'in bir job'i "PROCESSING" olarak kilitli tutabilecegi azami
    # sure. Worker bu sure dolmadan bitiremeden coker/kaybolursa, baska bir
    # worker lease suresi dolduktan sonra job'i devralabilir (bulgu,
    # 16 Temmuz 2026: eskiden bu sinir yoktu, coken worker'in job'i sonsuza
    # kadar kilitli kalabiliyordu). Gozlemlenen en uzun pipeline calismasindan
    # (OCR+LLM veya video) cok daha genis tutulur.
    idempotency_lease_seconds: int = 600  # 10 dakika

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

    # Video analysis: Roboflow (ADR-004 SS16 - Yusuf'un serbest model secimi)
    roboflow_api_key: str = ""
    roboflow_api_url: str = "https://serverless.roboflow.com"
    roboflow_logistics_model_id: str = "logistics-sz9jr/2"
    roboflow_damage_model_id: str = "detecting-a-damaged-parcel/11"
    roboflow_min_confidence: float = 0.4
    roboflow_timeout_seconds: float = 30.0

    # Video frame ornekleme (maliyet/sure sinirlamasi)
    video_frame_sample_interval_seconds: float = 1.0
    video_max_sampled_frames: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
