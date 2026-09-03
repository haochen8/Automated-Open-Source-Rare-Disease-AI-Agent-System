"""Conservative local model sizing policy."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from rare_disease_agent.models.hardware import HardwareInfo


class ModelCandidate(BaseModel):
    model: str
    backend: Literal["ollama", "mlx", "llama.cpp"]
    parameters_billion: float = Field(gt=0)
    quantization: str
    approximate_weight_gb: float = Field(gt=0)
    approximate_runtime_memory_gb: float = Field(gt=0)
    launch_ready_now: bool
    notes: str


class Recommendation(BaseModel):
    hardware: HardwareInfo
    local_parameter_ceiling_billion: float
    candidates: list[ModelCandidate]
    warnings: list[str]
    policy: list[str]


class ModelSuitability(BaseModel):
    model: str
    hardware: HardwareInfo
    parameters_billion: float | None
    quantization: str | None
    approximate_runtime_memory_gb: float | None
    local_parameter_ceiling_billion: float
    matched_catalog: bool
    override_used: bool
    allowed: bool
    reasons: list[str]


_CATALOG = (
    {
        "model": "qwen2.5:7b-instruct-q4_K_M",
        "backend": "ollama",
        "parameters_billion": 7.6,
        "quantization": "Q4_K_M",
        "approximate_weight_gb": 4.7,
        "approximate_runtime_memory_gb": 6.5,
        "notes": "Default development profile; suitable for structured output experiments.",
    },
    {
        "model": "llama3.1:8b-instruct-q4_K_M",
        "backend": "ollama",
        "parameters_billion": 8.0,
        "quantization": "Q4_K_M",
        "approximate_weight_gb": 4.9,
        "approximate_runtime_memory_gb": 6.8,
        "notes": "Alternative 8B family for workflow-specific benchmarking.",
    },
    {
        "model": "mistral:7b-instruct-q4_K_M",
        "backend": "ollama",
        "parameters_billion": 7.2,
        "quantization": "Q4_K_M",
        "approximate_weight_gb": 4.4,
        "approximate_runtime_memory_gb": 6.2,
        "notes": "Compact alternative with tool/function-calling support.",
    },
    {
        "model": "qwen2.5:14b-instruct-q4_K_M",
        "backend": "ollama",
        "parameters_billion": 14.0,
        "quantization": "Q4_K_M",
        "approximate_weight_gb": 9.0,
        "approximate_runtime_memory_gb": 11.5,
        "notes": "Use only with at least 24 GB memory or an explicitly constrained context.",
    },
)


def _parameter_ceiling(memory_gb: float) -> float:
    if memory_gb < 12:
        return 3.0
    if memory_gb < 24:
        return 8.0
    return 14.0


def recommend_models(hardware: HardwareInfo) -> Recommendation:
    """Return local-only recommendations capped at the 14B development policy."""

    ceiling = _parameter_ceiling(hardware.total_memory_gb)
    available_budget = max(0.0, hardware.available_memory_gb - 2.0)
    candidates = [
        ModelCandidate(
            **entry,
            launch_ready_now=entry["approximate_runtime_memory_gb"] <= available_budget,
        )
        for entry in _CATALOG
        if entry["parameters_billion"] <= ceiling
    ]
    warnings: list[str] = []
    if hardware.is_apple_silicon:
        warnings.append(
            "Unified memory is shared by macOS, applications, the model, and its context cache."
        )
    if not candidates:
        warnings.append(
            "No 7B-class model safely matches total memory; use a 1B-3B mock/development model."
        )
    elif not any(candidate.launch_ready_now for candidate in candidates):
        warnings.append(
            "Free memory is currently low; close memory-heavy applications before launch."
        )
    if hardware.free_disk_gb < 20:
        warnings.append(
            "Less than 20 GB disk space is free; do not download additional model weights."
        )

    return Recommendation(
        hardware=hardware,
        local_parameter_ceiling_billion=ceiling,
        candidates=candidates,
        warnings=warnings,
        policy=[
            "Run only one large local LLM at a time.",
            "Share the orchestrator model with investigator and critic during development.",
            "Prefer Q4 quantization and an initially modest context window.",
            "Use mock LLMs in automated tests.",
            "Keep deterministic bioinformatics outside LLM inference.",
            "Use a configurable remote/self-hosted backend for larger production models.",
        ],
    )


def check_model_suitability(
    model: str, hardware: HardwareInfo, *, allow_oversized: bool = False
) -> ModelSuitability:
    """Check a named local model without contacting a registry or starting a runtime."""

    catalog_entry = next(
        (entry for entry in _CATALOG if entry["model"].lower() == model.lower()), None
    )
    matched = catalog_entry is not None
    if catalog_entry:
        parameters = float(catalog_entry["parameters_billion"])
        quantization = str(catalog_entry["quantization"])
        runtime_memory = float(catalog_entry["approximate_runtime_memory_gb"])
    else:
        match = re.search(r"(?::|[-_])(\d+(?:\.\d+)?)b\b", model, flags=re.IGNORECASE)
        parameters = float(match.group(1)) if match else None
        quantization_match = re.search(r"q\d(?:_[A-Za-z0-9]+)*", model, flags=re.IGNORECASE)
        quantization = quantization_match.group(0).upper() if quantization_match else None
        runtime_memory = round(parameters * 0.7 + 1.2, 1) if parameters else None

    ceiling = _parameter_ceiling(hardware.total_memory_gb)
    reasons: list[str] = []
    if parameters is None or runtime_memory is None:
        reasons.append(
            "Parameter count could not be inferred; add the model to the curated catalog."
        )
    elif parameters > 14:
        reasons.append("Models above the 14B development cap are never approved for local use.")
    elif parameters > ceiling and not allow_oversized:
        reasons.append(
            f"{parameters:g}B exceeds this host's conservative {ceiling:g}B ceiling; "
            "an explicit override is required."
        )
    if quantization is None or not quantization.startswith("Q4"):
        reasons.append("The local development profile requires a known Q4 quantization.")
    if runtime_memory is not None and runtime_memory + 2 > hardware.available_memory_gb:
        reasons.append(
            f"Approximately {runtime_memory + 2:.1f} GB free memory is required including "
            f"headroom; only {hardware.available_memory_gb:.1f} GB is available."
        )
    allowed = not reasons
    return ModelSuitability(
        model=model,
        hardware=hardware,
        parameters_billion=parameters,
        quantization=quantization,
        approximate_runtime_memory_gb=runtime_memory,
        local_parameter_ceiling_billion=ceiling,
        matched_catalog=matched,
        override_used=allow_oversized,
        allowed=allowed,
        reasons=reasons or ["Model fits the configured local-development safety policy."],
    )
