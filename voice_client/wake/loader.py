"""Ленивая загрузка wake-модели вне Qt UI thread."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from voice_client.net.protocol import SpeechLanguage

from .base import WakeWordEngine

WakeEngineFactory = Callable[[SpeechLanguage, str], WakeWordEngine]


class WakeEngineLoader(QObject):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, factory: WakeEngineFactory) -> None:
        super().__init__()
        self._factory = factory
        self._lock = threading.Lock()
        self._generation = 0
        self._closed = False

    def load(self, language: SpeechLanguage, phrase: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            generation = self._generation
        threading.Thread(
            target=self._load,
            args=(generation, language, phrase),
            name="voice-wake-loader",
            daemon=True,
        ).start()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1

    def _load(self, generation: int, language: SpeechLanguage, phrase: str) -> None:
        try:
            engine = self._factory(language, phrase)
        except Exception as exc:
            if self._current(generation):
                self.failed.emit(str(exc) or type(exc).__name__)
            return
        if not self._current(generation):
            engine.close()
            return
        self.loaded.emit(engine)

    def _current(self, generation: int) -> bool:
        with self._lock:
            return not self._closed and generation == self._generation


__all__ = ["WakeEngineFactory", "WakeEngineLoader"]
