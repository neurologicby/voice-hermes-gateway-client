"""Локальный wake phrase поверх лицензированных streaming ASR моделей Sherpa."""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from .base import WakeWordEngine, normalize_phrase


class SherpaStream(Protocol):
    def accept_waveform(self, sample_rate: int, samples: Any) -> None: ...


class SherpaRecognizer(Protocol):
    def create_stream(self) -> SherpaStream: ...

    def is_ready(self, stream: SherpaStream) -> bool: ...

    def decode_stream(self, stream: SherpaStream) -> None: ...

    def get_result(self, stream: SherpaStream) -> Any: ...


class SherpaPhraseWakeEngine(WakeWordEngine):
    """Ищет явную RU/EN фразу в локальных streaming partial transcripts."""

    def __init__(
        self,
        phrase: str,
        recognizer: SherpaRecognizer,
        *,
        model_sample_rate: int = 16_000,
    ) -> None:
        super().__init__(phrase)
        if model_sample_rate not in {8_000, 16_000}:
            raise ValueError("wake model sample rate must be 8000 or 16000")
        self._recognizer = recognizer
        self._model_sample_rate = model_sample_rate
        self._stream = recognizer.create_stream()
        self._last_text = ""

    def process(self, pcm_s16le: bytes) -> bool:
        if len(pcm_s16le) % 2:
            raise ValueError("wake PCM must contain complete int16 samples")
        if not pcm_s16le:
            return False
        samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
        if self._model_sample_rate == 8_000:
            usable = samples.size - samples.size % 2
            if usable == 0:
                return False
            samples = samples[:usable].reshape(-1, 2).mean(axis=1)
        self._stream.accept_waveform(self._model_sample_rate, samples)
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        text = normalize_phrase(str(self._recognizer.get_result(self._stream).text))
        if not text or text == self._last_text:
            return False
        self._last_text = text
        words = f" {text} "
        if f" {self.phrase} " not in words:
            return False
        self.reset()
        return True

    def reset(self) -> None:
        self._stream = self._recognizer.create_stream()
        self._last_text = ""


__all__ = ["SherpaPhraseWakeEngine", "SherpaRecognizer", "SherpaStream"]
