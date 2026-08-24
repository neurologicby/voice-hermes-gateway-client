"""Запускаемый entry point Windows VoiceGateway Client."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path
from typing import cast
from uuid import uuid4

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from voice_client.audio.devices import AudioDeviceScanner
from voice_client.audio.player import AudioPlayer
from voice_client.audio.recorder import MicrophoneRecorder
from voice_client.audio.vad import build_silero_vad_engine
from voice_client.file_transfer import FileTransferLoader
from voice_client.history import HistoryStore
from voice_client.net.protocol import SpeechLanguage
from voice_client.net.ws_client import VoiceWSClient
from voice_client.qt_worker import AsyncioNetworkWorker
from voice_client.ui import MainWindow
from voice_client.wake import WakeEngineLoader, WakeResources, build_sherpa_phrase_engine


def create_window(settings: QSettings | None = None) -> MainWindow:
    """Собирает независимые client-компоненты; модели при старте не загружает."""

    config = settings or QSettings("VoiceGateway", "VoiceClient")
    device_id = _setting(config, "identity/device_id", str(uuid4()))
    config.setValue("identity/device_id", device_id)
    url = _setting(config, "connection/url", "ws://127.0.0.1:8765/ws")
    user_name = _setting(config, "identity/user_name", getpass.getuser())
    language = _setting(config, "speech/language", "ru")
    if language not in {"ru", "en"}:
        language = "ru"
    microphone_device = _int_setting(config, "audio/input_device")
    output_device = _int_setting(config, "audio/output_device")
    wake_phrase = _setting(config, "wake/phrase", "Привет Гермес")
    workspace_root = Path(__file__).resolve().parents[2]
    wake_model_dirs = {
        "ru": Path(
            _setting(
                config,
                "wake/ru_model_dir",
                str(
                    workspace_root
                    / "plugin/models/sherpa-onnx-streaming-t-one-russian-2025-09-08"
                ),
            )
        ),
        "en": Path(
            _setting(
                config,
                "wake/en_model_dir",
                str(
                    workspace_root
                    / "plugin/models/sherpa-onnx-streaming-zipformer-en-2023-06-26-int8"
                ),
            )
        ),
    }
    wake_vad_dir = Path(
        _setting(
            config,
            "wake/vad_model_dir",
            str(workspace_root / "plugin/models/silero-vad-v5"),
        )
    )

    def wake_factory(selected: SpeechLanguage, phrase: str) -> WakeResources:
        return WakeResources(
            build_sherpa_phrase_engine(selected, phrase, wake_model_dirs[selected]),
            build_silero_vad_engine(wake_vad_dir),
        )

    player = AudioPlayer(device=output_device)
    client = VoiceWSClient(
        url,
        device_id=device_id,
        user=user_name,
        playback=player,
        language=cast(SpeechLanguage, language),
    )
    worker = AsyncioNetworkWorker(client)
    recorder = MicrophoneRecorder(device=microphone_device)
    history = HistoryStore()
    window = MainWindow(
        worker=worker,
        recorder=recorder,
        history=history,
        url=url,
        device_id=device_id,
        user_name=user_name,
        on_close=player.close,
        file_loader=FileTransferLoader(),
        device_scanner=AudioDeviceScanner(),
        on_output_device=player.set_device,
        microphone_device=microphone_device,
        output_device=output_device,
        wake_loader=WakeEngineLoader(wake_factory),
        wake_phrase=wake_phrase,
    )
    window.language_combo.setCurrentIndex(0 if language == "ru" else 1)
    window.language_combo.currentIndexChanged.connect(
        lambda: config.setValue("speech/language", window.language_combo.currentData())
    )
    window.microphone_combo.currentIndexChanged.connect(
        lambda: config.setValue("audio/input_device", window.microphone_combo.currentData())
    )
    window.output_combo.currentIndexChanged.connect(
        lambda: config.setValue("audio/output_device", window.output_combo.currentData())
    )
    window.wake_phrase_edit.editingFinished.connect(
        lambda: config.setValue("wake/phrase", window.wake_phrase_edit.text().strip())
    )
    window.wake_button.toggled.connect(lambda on: config.setValue("wake/enabled", on))
    if _bool_setting(config, "wake/enabled"):
        window.wake_button.setChecked(True)
    return window


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("VoiceGateway Client")
    app.setOrganizationName("VoiceGateway")
    window = create_window()
    window.show()
    window.start()
    return app.exec()


def _setting(settings: QSettings, key: str, default: str) -> str:
    value = settings.value(key, default)
    return value if isinstance(value, str) and value else default


def _int_setting(settings: QSettings, key: str) -> int | None:
    value = settings.value(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _bool_setting(settings: QSettings, key: str) -> bool:
    value = settings.value(key, False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["create_window", "main"]


if __name__ == "__main__":  # pragma: no cover - desktop entry point
    raise SystemExit(main())
