"""Ollama diagnostics and explicit-consent setup helpers."""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import Any, Iterator

import httpx

OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_LICENSE_URL = "https://github.com/ollama/ollama/blob/main/LICENSE"
OLLAMA_INSTALLER_MAX_BYTES = 512 * 1024 * 1024
MODEL_PULL_MAX_BYTES = 20 * 1024 * 1024 * 1024


class SetupError(RuntimeError):
    """A setup action was refused or could not be completed safely."""


class SetupCancelled(SetupError):
    """A caller cancelled an in-progress setup action."""


@dataclass(frozen=True)
class SetupOffer:
    kind: str
    source: str
    license_url: str
    destination: str
    size: str
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def installer_offer(destination: Path | str) -> SetupOffer:
    return SetupOffer(
        kind="ollama_installer",
        source=OLLAMA_INSTALLER_URL,
        license_url=OLLAMA_LICENSE_URL,
        destination=str(Path(destination)),
        size="up to 512 MB",
    )


def model_offer(model: str, base_url: str, destination: str = "Ollama-managed local model store") -> SetupOffer:
    approximate = {"qwen2.5:3b": "approximately 2.0 GB", "qwen2.5:7b": "approximately 4.7 GB", "qwen2.5:14b": "approximately 9.0 GB"}
    return SetupOffer(
        kind="model",
        source=f"{base_url.rstrip('/')}/api/pull",
        license_url="https://ollama.com/library",
        destination=destination,
        size=approximate.get(model, "size reported by Ollama while downloading"),
        model=model,
    )


def _write_bounded_response(
    chunks: Iterator[bytes],
    destination: Path,
    max_bytes: int,
    cancel_event: Event | None = None,
) -> int:
    written = 0
    try:
        with destination.open("wb") as output:
            for chunk in chunks:
                if cancel_event is not None and cancel_event.is_set():
                    raise SetupCancelled("Setup was cancelled; the partial download was removed.")
                written += len(chunk)
                if written > max_bytes:
                    raise SetupError(f"Download exceeded the {max_bytes} byte safety limit.")
                output.write(chunk)
    except (OSError, httpx.HTTPError) as exc:
        if isinstance(exc, SetupError):
            raise
        raise SetupError("Download failed; no partial file was kept.") from exc
    return written


def stage_ollama_installer(
    destination: Path | str,
    *,
    consent: bool,
    client: httpx.Client | None = None,
    cancel_event: Event | None = None,
) -> Path:
    """Download, with consent, a bounded installer into a staging directory.

    The returned executable is never launched. Existing files are not replaced.
    """
    if not consent:
        raise SetupError("Explicit consent is required before downloading Ollama.")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SetupError("An Ollama installer is already staged at the destination.")
    staged = target.with_name(f".{target.name}.part")
    try:
        http_client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        close_client = client is None
        try:
            with http_client.stream("GET", OLLAMA_INSTALLER_URL) as response:
                response.raise_for_status()
                _write_bounded_response(response.iter_bytes(64 * 1024), staged, OLLAMA_INSTALLER_MAX_BYTES, cancel_event)
        finally:
            if close_client:
                http_client.close()
        staged.replace(target)
        return target
    except SetupError:
        staged.unlink(missing_ok=True)
        raise
    except (OSError, httpx.HTTPError) as exc:
        staged.unlink(missing_ok=True)
        raise SetupError("Ollama installer download failed; no partial file was kept.") from exc


def pull_model(
    model: str,
    base_url: str,
    *,
    consent: bool,
    client: httpx.Client | None = None,
    cancel_event: Event | None = None,
) -> list[dict[str, Any]]:
    """Ask an existing local Ollama service to pull a model after consent."""
    if not consent:
        raise SetupError("Explicit consent is required before downloading a model.")
    if not model.strip():
        raise SetupError("A model name is required.")
    http_client = client or httpx.Client(timeout=None)
    close_client = client is None
    events: list[dict[str, Any]] = []
    total = 0
    try:
        with http_client.stream("POST", f"{base_url.rstrip('/')}/api/pull", json={"name": model}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if cancel_event is not None and cancel_event.is_set():
                    raise SetupCancelled("Model download cancelled; Ollama may resume it safely.")
                total += len(line.encode("utf-8"))
                if total > MODEL_PULL_MAX_BYTES:
                    raise SetupError("Model progress exceeded the safety limit.")
                if line:
                    try:
                        events.append(httpx.Response(200, content=line).json())
                    except ValueError:
                        raise SetupError("Ollama returned invalid model progress.") from None
    except SetupError:
        raise
    except httpx.HTTPError as exc:
        raise SetupError("Model download failed; check that Ollama is running and reachable.") from exc
    finally:
        if close_client:
            http_client.close()
    return events


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
