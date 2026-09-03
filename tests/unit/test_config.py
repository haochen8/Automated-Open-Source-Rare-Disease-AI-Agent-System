from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from rare_disease_agent.config import Settings, load_settings


def test_default_config_shares_one_local_model() -> None:
    settings = load_settings(Path("configs/default.yaml"))

    assert settings.models.orchestrator.backend == "ollama"
    assert settings.models.orchestrator.max_local_parameters_billion == 14
    assert settings.models.investigator.reuse_orchestrator_model is True
    assert settings.models.critic.reuse_orchestrator_model is True
    assert settings.privacy.allow_remote_patient_data is False
    assert settings.agents.variant_filtering.max_iterations == 10
    assert settings.agents.variant_filtering.minimum_candidate_count == 25


def test_environment_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RDA_MODELS__ORCHESTRATOR__BACKEND", "llama_cpp")

    settings = load_settings(Path("configs/default.yaml"))

    assert settings.models.orchestrator.backend == "llama_cpp"


def test_shared_role_cannot_select_second_model() -> None:
    with pytest.raises(ValidationError, match="reuse_orchestrator_model"):
        Settings(
            models={
                "investigator": {
                    "reuse_orchestrator_model": True,
                    "model": "another-large-model",
                }
            }
        )


def test_non_mapping_yaml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(["not", "a", "mapping"]), encoding="utf-8")

    with pytest.raises(ValueError, match="root must be a mapping"):
        load_settings(path)
