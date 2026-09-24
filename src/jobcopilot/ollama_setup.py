"""Read-only local Ollama detection and conservative model recommendations.

This module deliberately never installs, starts, stops, or downloads anything.
It is safe to call from the first-run/settings UI before a setup wizard exists.
"""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class HardwareInfo:
    memory_gb: float | None
    free_storage_gb: float | None
    source: str


@dataclass(frozen=True)
class ModelRecommendation:
    model: str
    reason: str
    confidence: str


@dataclass(frozen=True)
class InstalledModel:
    name: str
    size_gb: float | None
    modified_at: str | None


@dataclass(frozen=True)
class OllamaDiagnostics:
    executable_path: str | None
    executable_version: str | None
    service_reachable: bool
    service_error: str | None
    installed_models: tuple[InstalledModel, ...]
    configured_model: str
    configured_model_available: bool
    hardware: HardwareInfo
    recommendation: ModelRecommendation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _memory_gb() -> float | None:
    if platform.system() == "Windows":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total", ctypes.c_ulonglong),
                ("available", ctypes.c_ulonglong),
                ("pagefile_total", ctypes.c_ulonglong),
                ("pagefile_available", ctypes.c_ulonglong),
                ("virtual_total", ctypes.c_ulonglong),
                ("virtual_available", ctypes.c_ulonglong),
                ("extended", ctypes.c_ulong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total / (1024**3)
        return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / (1024**3)
    except (AttributeError, OSError, ValueError):
        return None


def detect_hardware(data_path: Path | str = ".") -> HardwareInfo:
    """Return best-effort hardware facts; unknown values remain ``None``."""
    try:
        free_storage = shutil.disk_usage(data_path).free / (1024**3)
    except OSError:
        free_storage = None
    return HardwareInfo(_memory_gb(), free_storage, platform.system() or "unknown")


def recommend_model(hardware: HardwareInfo) -> ModelRecommendation:
    """Choose a modest default using RAM/storage thresholds, not benchmarks.

    The thresholds are intentionally conservative: model sizes are approximate,
    quantization varies, and context length/GPU availability are not detected.
    Users can always keep or choose another model in Settings.
    """
    memory = hardware.memory_gb
    storage = hardware.free_storage_gb
    if storage is not None and storage < 10:
        return ModelRecommendation(
            "qwen2.5:3b",
            "Free storage is below 10 GB; choose a small model to avoid filling the disk.",
            "low",
        )
    if memory is None:
        return ModelRecommendation(
            "qwen2.5:7b",
            "Memory could not be detected; 7B is the conservative general-purpose default.",
            "low",
        )
    if memory < 8:
        return ModelRecommendation(
            "qwen2.5:3b",
            "Systems with under 8 GB RAM should start with a small model.",
            "medium",
        )
    if memory < 16:
        return ModelRecommendation(
            "qwen2.5:7b",
            "7B is a conservative fit for systems with 8–16 GB RAM.",
            "medium",
        )
    return ModelRecommendation(
        "qwen2.5:14b",
        "14B is a quality-oriented default for systems with at least 16 GB RAM.",
        "medium",
    )


def _executable_details(path: str | None) -> str | None:
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr).strip()
    return output or None


def detect_ollama(
    configured_model: str,
    base_url: str = "http://localhost:11434",
    data_path: Path | str = ".",
    timeout: float = 2.0,
) -> OllamaDiagnostics:
    """Inspect the executable and local service without changing machine state."""
    executable = shutil.which("ollama")
    models: list[InstalledModel] = []
    service_error: str | None = None
    reachable = False
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("models", []):
            name = item.get("name")
            if not name:
                continue
            size = item.get("size")
            models.append(
                InstalledModel(
                    name=name,
                    size_gb=float(size) / (1024**3) if isinstance(size, (int, float)) else None,
                    modified_at=item.get("modified_at"),
                )
            )
        reachable = True
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        service_error = str(exc) or exc.__class__.__name__

    hardware = detect_hardware(data_path)
    recommendation = recommend_model(hardware)
    available_names = {model.name for model in models}
    return OllamaDiagnostics(
        executable_path=executable,
        executable_version=_executable_details(executable),
        service_reachable=reachable,
        service_error=service_error,
        installed_models=tuple(models),
        configured_model=configured_model,
        configured_model_available=configured_model in available_names,
        hardware=hardware,
        recommendation=recommendation,
    )
