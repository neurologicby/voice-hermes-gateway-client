"""Асинхронное перечисление PortAudio-устройств для Qt UI."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: int
    name: str
    input_channels: int
    output_channels: int


class AudioDeviceScanner(QObject):
    scanned = Signal(object)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._lock = threading.Lock()

    def refresh(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        threading.Thread(
            target=self._scan,
            name="voice-device-scan",
            daemon=True,
        ).start()

    def _scan(self) -> None:
        try:
            self.scanned.emit(query_audio_devices())
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)
        finally:
            with self._lock:
                self._running = False


def query_audio_devices() -> list[AudioDevice]:
    try:
        import sounddevice  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("install voice-gateway-client[audio] for device discovery") from exc
    raw_devices: Any = sounddevice.query_devices()
    devices = []
    for index, raw in enumerate(raw_devices):
        info = dict(raw)
        devices.append(
            AudioDevice(
                index=index,
                name=str(info.get("name", f"Device {index}")),
                input_channels=int(info.get("max_input_channels", 0)),
                output_channels=int(info.get("max_output_channels", 0)),
            )
        )
    return devices


__all__ = ["AudioDevice", "AudioDeviceScanner", "query_audio_devices"]
