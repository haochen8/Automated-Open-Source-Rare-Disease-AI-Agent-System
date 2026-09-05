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


class VariantFilteringAgentConfig(BaseModel):
    max_iterations: int = Field(default=10, ge=1, le=100)
    max_tool_calls: int = Field(default=20, ge=1, le=200)
    target_candidate_count: int = Field(default=250, ge=1)
    minimum_candidate_count: int = Field(default=25, ge=1)
    max_invalid_decisions: int = Field(default=3, ge=1, le=20)
    max_tool_errors: int = Field(default=3, ge=1, le=20)
    max_no_reduction_attempts: int = Field(default=3, ge=1, le=20)
    max_repeated_decisions: int = Field(default=2, ge=1, le=20)
    sample_size: int = Field(default=5, ge=1, le=25)


class AgentsConfig(BaseModel):
    variant_filtering: VariantFilteringAgentConfig = Field(
        default_factory=VariantFilteringAgentConfig
    )


class PreliminaryScoringConfig(BaseModel):
    quality: float = Field(default=0.10, ge=0, le=1)
    rarity: float = Field(default=0.20, ge=0, le=1)
    consequence: float = Field(default=0.15, ge=0, le=1)
    phenotype: float = Field(default=0.30, ge=0, le=1)
    inheritance: float = Field(default=0.25, ge=0, le=1)

    @model_validator(mode="after")
    def weights_must_sum_to_one(self) -> PreliminaryScoringConfig:
        if abs(sum(self.model_dump().values()) - 1.0) > 1e-9:
            raise ValueError("track1.preliminary_scoring weights must sum to 1.0")
        return self


class Track1Config(BaseModel):
    preliminary_scoring: PreliminaryScoringConfig = Field(default_factory=PreliminaryScoringConfig)
    phenotype_priority_threshold: float = Field(default=0.35, ge=0, le=1)
    inheritance_priority_threshold: float = Field(default=0.80, ge=0, le=1)
    minimum_genotype_quality: float = Field(default=20, ge=0)
    evidence_summary_limit: int = Field(default=10, ge=1, le=25)


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
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    track1: Track1Config = Field(default_factory=Track1Config)

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
