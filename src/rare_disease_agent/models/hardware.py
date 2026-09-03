"""Cross-platform, CUDA-free hardware inspection."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

import psutil
from pydantic import BaseModel, Field

GIB = 1024**3


class HardwareInfo(BaseModel):
    operating_system: str
    architecture: str
    cpu: str
    total_memory_gb: float = Field(ge=0)
    available_memory_gb: float = Field(ge=0)
    unified_memory_gb: float | None = Field(default=None, ge=0)
    gpu: str
    metal_supported: bool
    cuda_assumed: bool = False
    free_disk_gb: float = Field(ge=0)

    @property
    def is_apple_silicon(self) -> bool:
        return self.operating_system == "macOS" and self.architecture in {"arm64", "aarch64"}


def _sysctl(name: str) -> str | None:
    try:
        result = subprocess.run(
            ["sysctl", "-n", name],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _is_arm64_darwin_host() -> bool:
    """Detect the physical host even when the Python process uses Rosetta."""

    return platform.system() == "Darwin" and (
        platform.machine().lower() in {"arm64", "aarch64"}
        or "ARM64" in platform.version().upper()
        or _sysctl("hw.optional.arm64") == "1"
    )


def _apple_chip() -> str | None:
    """Return only the chip name, including when Python runs under Rosetta."""

    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        entries = json.loads(result.stdout).get("SPDisplaysDataType", [])
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    for entry in entries:
        chip = entry.get("sppci_model") or entry.get("_name")
        if chip and str(chip).startswith("Apple "):
            return str(chip)
    return None


def _cpu_name(apple_chip: str | None) -> str:
    if apple_chip:
        return apple_chip
    if platform.system() == "Darwin":
        return _sysctl("machdep.cpu.brand_string") or platform.processor() or "unknown Mac CPU"
    return platform.processor() or platform.machine() or "unknown"


def _total_memory_bytes() -> int:
    measured = int(psutil.virtual_memory().total)
    if measured > 0:
        return measured
    if platform.system() == "Darwin":
        raw = _sysctl("hw.memsize")
        if raw and raw.isdigit():
            return int(raw)
    pages = os.sysconf("SC_PHYS_PAGES")
    page_size = os.sysconf("SC_PAGE_SIZE")
    return int(pages * page_size)


def inspect_hardware(disk_path: Path | str = ".") -> HardwareInfo:
    """Inspect resources without starting a model server or downloading weights."""

    system = platform.system()
    architecture = platform.machine().lower()
    arm64_darwin_host = _is_arm64_darwin_host()
    apple_chip = _apple_chip()
    if arm64_darwin_host:
        # An x86_64 Python process under Rosetta otherwise hides the host architecture.
        architecture = "arm64"
    total_bytes = _total_memory_bytes()
    available_bytes = int(psutil.virtual_memory().available)
    apple_silicon = system == "Darwin" and architecture in {"arm64", "aarch64"}
    disk = shutil.disk_usage(Path(disk_path).resolve())
    return HardwareInfo(
        operating_system="macOS" if system == "Darwin" else system,
        architecture=architecture,
        cpu=_cpu_name(apple_chip),
        total_memory_gb=round(total_bytes / GIB, 1),
        available_memory_gb=round(available_bytes / GIB, 1),
        unified_memory_gb=round(total_bytes / GIB, 1) if apple_silicon else None,
        gpu="Apple integrated GPU (Metal)" if apple_silicon else "not automatically detected",
        metal_supported=apple_silicon,
        cuda_assumed=False,
        free_disk_gb=round(disk.free / GIB, 1),
    )
