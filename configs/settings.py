"""
Centralised settings loader.

Resolution order (highest precedence last):
  configs/base.yaml → configs/{ENV}.yaml → environment variables
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIGS_DIR = Path(__file__).parent


def _load_yaml_layer(name: str) -> dict:
    path = _CONFIGS_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _merged_yaml_defaults() -> dict:
    base = _load_yaml_layer("base")
    env = os.getenv("ENV", "dev")
    overlay = _load_yaml_layer(env)
    # Shallow merge — nested keys in overlay win
    return {**base, **overlay}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Runtime environment
    env: str = Field(default="dev")

    # OpenAI
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o")
    openai_temperature: float = Field(default=0.0)

    # Redis
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[assignment]
    redis_ttl_seconds: int = Field(default=86400)

    # Kubernetes
    k8s_in_cluster: bool = Field(default=False)
    k8s_kubeconfig_path: str = Field(default="~/.kube/config")
    k8s_namespace: str = Field(default="default")

    # Prometheus
    prometheus_url: str = Field(default="http://localhost:9090")

    # API
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_key: str = Field(default="change-me")

    # Human-in-the-loop
    approval_timeout_seconds: int = Field(default=300)
    approval_webhook_url: str = Field(default="")

    # Observability
    log_level: str = Field(default="INFO")
    otel_exporter_otlp_endpoint: str = Field(default="http://localhost:4317")
    otel_service_name: str = Field(default="auto-sre-agent")

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):  # type: ignore[override]
        sources = super().settings_customise_sources(settings_cls, **kwargs)
        return sources

    @property
    def is_production(self) -> bool:
        return self.env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    defaults = _merged_yaml_defaults()
    # Only use YAML values as fallback — skip any key already set in the
    # actual environment so that docker-compose / k8s env vars always win.
    env_keys = {k.lower() for k in os.environ}
    filtered = {k: v for k, v in defaults.items() if k.lower() not in env_keys}
    return Settings(**filtered)
