"""Typed project configuration with YAML and environment overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class PathsConfig(BaseModel):
    restricted_data_dir: Path = Path("../rare-disease-private")
    public_cache_dir: Path = Path("cache/public")
    run_dir: Path = Path("runs/private")


class PrivacyConfig(BaseModel):
    allow_remote_patient_data: bool = False
    redact_sensitive_logs: bool = True


class ModelRoleConfig(BaseModel):
    backend: str = "ollama"
    model: str | None = None
    size_class: Literal["tiny", "small", "medium", "large", "remote"] = "small"
    max_local_parameters_billion: float = Field(default=14.0, gt=0)
    base_url: str | None = None
    reuse_orchestrator_model: bool = False


class EmbeddingConfig(BaseModel):
    backend: str = "sentence_transformers"
    model: str = "NeuML/pubmedbert-base-embeddings"
    device: str = "auto"


class ModelsConfig(BaseModel):
    orchestrator: ModelRoleConfig = Field(
        default_factory=lambda: ModelRoleConfig(model="qwen2.5:7b-instruct-q4_K_M")
    )
    investigator: ModelRoleConfig = Field(
        default_factory=lambda: ModelRoleConfig(reuse_orchestrator_model=True)
    )
    critic: ModelRoleConfig = Field(
        default_factory=lambda: ModelRoleConfig(reuse_orchestrator_model=True)
    )
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)

    @model_validator(mode="after")
    def shared_roles_do_not_select_another_model(self) -> ModelsConfig:
        for role_name in ("investigator", "critic"):
            role = getattr(self, role_name)
            if role.reuse_orchestrator_model and role.model is not None:
                raise ValueError(
                    f"models.{role_name} cannot set model while reuse_orchestrator_model is true"
                )
        return self


class PipelineConfig(BaseModel):
    maximum_agent_iterations: int = Field(default=20, ge=1)
    maximum_tool_calls: int = Field(default=100, ge=1)
    timeout_seconds: int = Field(default=3600, ge=1)
    random_seed: int = 42


class Settings(BaseSettings):
    """Application settings.

    Environment variables use ``RDA_`` and ``__`` for nesting, for example
    ``RDA_MODELS__ORCHESTRATOR__BACKEND=vllm``. Environment values take
    precedence over values loaded from YAML.
    """

    model_config = SettingsConfigDict(
        env_prefix="RDA_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    paths: PathsConfig = Field(default_factory=PathsConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return env_settings, init_settings, dotenv_settings, file_secret_settings


def load_settings(path: Path | str = Path("configs/default.yaml")) -> Settings:
    """Load validated settings from YAML, with ``RDA_*`` environment overrides."""

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration root must be a mapping: {config_path}")
    return Settings(**raw)
