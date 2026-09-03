from rare_disease_agent.models.hardware import HardwareInfo
from rare_disease_agent.models.recommend import recommend_models


def apple_hardware(total: float, available: float, disk: float = 100) -> HardwareInfo:
    return HardwareInfo(
        operating_system="macOS",
        architecture="arm64",
        cpu="Apple M2",
        total_memory_gb=total,
        available_memory_gb=available,
        unified_memory_gb=total,
        gpu="Apple integrated GPU (Metal)",
        metal_supported=True,
        cuda_assumed=False,
        free_disk_gb=disk,
    )


def test_m2_16gb_recommends_only_7b_to_8b_q4_models() -> None:
    recommendation = recommend_models(apple_hardware(16, 10))

    assert recommendation.local_parameter_ceiling_billion == 8
    assert recommendation.candidates
    assert all(candidate.parameters_billion <= 8 for candidate in recommendation.candidates)
    assert all(candidate.quantization.startswith("Q4") for candidate in recommendation.candidates)
    assert all(
        candidate.backend in {"ollama", "mlx", "llama.cpp"}
        for candidate in recommendation.candidates
    )


def test_14b_requires_at_least_24gb_total_memory() -> None:
    small = recommend_models(apple_hardware(16, 14))
    larger = recommend_models(apple_hardware(24, 20))

    assert not any(candidate.parameters_billion > 8 for candidate in small.candidates)
    assert any(candidate.parameters_billion == 14 for candidate in larger.candidates)
    assert all(candidate.parameters_billion <= 14 for candidate in larger.candidates)


def test_current_memory_controls_launch_readiness() -> None:
    recommendation = recommend_models(apple_hardware(16, 4))

    assert not any(candidate.launch_ready_now for candidate in recommendation.candidates)
    assert any("currently low" in warning for warning in recommendation.warnings)


def test_low_memory_host_does_not_recommend_oversized_model() -> None:
    recommendation = recommend_models(apple_hardware(8, 6))

    assert recommendation.candidates == []
    assert any("No 7B-class model" in warning for warning in recommendation.warnings)
