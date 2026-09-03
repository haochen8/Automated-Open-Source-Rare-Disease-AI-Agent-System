"""Conservative local model sizing policy."""

from __future__ import annotations

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
