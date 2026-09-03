from rare_disease_agent.models.hardware import HardwareInfo
from rare_disease_agent.models.recommend import check_model_suitability


def hardware(*, total: float = 16, available: float = 12) -> HardwareInfo:
    return HardwareInfo(
        operating_system="macOS",
        architecture="arm64",
        cpu="Apple M2",
        total_memory_gb=total,
        available_memory_gb=available,
        unified_memory_gb=total,
        gpu="Apple integrated GPU (Metal)",
        metal_supported=True,
        free_disk_gb=100,
    )


def test_known_7b_q4_is_allowed_with_live_headroom() -> None:
    result = check_model_suitability("qwen2.5:7b-instruct-q4_K_M", hardware(available=12))

    assert result.allowed is True
    assert result.matched_catalog is True


def test_14b_requires_override_and_live_headroom() -> None:
    ordinary = check_model_suitability("qwen2.5:14b-instruct-q4_K_M", hardware(available=15))
    overridden = check_model_suitability(
        "qwen2.5:14b-instruct-q4_K_M",
        hardware(available=15),
        allow_oversized=True,
    )

    assert ordinary.allowed is False
    assert overridden.allowed is True


def test_override_never_bypasses_live_memory_or_14b_cap() -> None:
    low_memory = check_model_suitability(
        "qwen2.5:14b-instruct-q4_K_M",
        hardware(available=5),
        allow_oversized=True,
    )
    frontier = check_model_suitability(
        "some-model:70b-instruct-q4_K_M",
        hardware(total=64, available=60),
        allow_oversized=True,
    )

    assert low_memory.allowed is False
    assert frontier.allowed is False
    assert any("never approved" in reason for reason in frontier.reasons)
