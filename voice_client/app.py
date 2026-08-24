"""Запускаемый entry point Windows VoiceGateway Client."""

from __future__ import annotations

import getpass
import sys
from typing import cast
from uuid import uuid4

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from voice_client.audio.player import AudioPlayer
from voice_client.audio.recorder import MicrophoneRecorder
from voice_client.history import HistoryStore
from voice_client.net.protocol import SpeechLanguage
from voice_client.net.ws_client import VoiceWSClient
from voice_client.qt_worker import AsyncioNetworkWorker
from voice_client.ui import MainWindow


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

    player = AudioPlayer()
    client = VoiceWSClient(
        url,
        device_id=device_id,
        user=user_name,
        playback=player,
        language=cast(SpeechLanguage, language),
    )
    worker = AsyncioNetworkWorker(client)
    recorder = MicrophoneRecorder()
    history = HistoryStore()
    window = MainWindow(
        worker=worker,
        recorder=recorder,
        history=history,
        url=url,
        device_id=device_id,
        user_name=user_name,
        on_close=player.close,
    )
    window.language_combo.setCurrentIndex(0 if language == "ru" else 1)
    window.language_combo.currentIndexChanged.connect(
        lambda: config.setValue("speech/language", window.language_combo.currentData())
    )
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


__all__ = ["create_window", "main"]


if __name__ == "__main__":  # pragma: no cover - desktop entry point
    raise SystemExit(main())
