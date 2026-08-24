"""Checksum-verified local Silero VAD for hands-free utterance finalization."""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

_MODEL_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
_LICENSE_SHA256 = "2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b"


@dataclass(frozen=True, slots=True)
class VADResult:
    speech_started: bool = False
    speech_ended: bool = False


class VADSession(Protocol):
    def accept_pcm(self, pcm_s16le: bytes) -> VADResult: ...

    def cancel(self) -> None: ...


class VADEngine(Protocol):
    def create_session(self, *, sample_rate: int) -> VADSession: ...


class SileroVADEngine:
    def __init__(
        self,
        model_path: Path,
        *,
        threshold: float = 0.5,
        min_silence_seconds: float = 0.6,
        min_speech_seconds: float = 0.1,
        sherpa_module: Any | None = None,
    ) -> None:
        if not 0 < threshold < 1:
            raise ValueError("VAD threshold must be between zero and one")
        self._model_path = model_path
        self._threshold = threshold
        self._min_silence_seconds = min_silence_seconds
        self._min_speech_seconds = min_speech_seconds
        self._sherpa = sherpa_module or importlib.import_module("sherpa_onnx")

    def create_session(self, *, sample_rate: int) -> SileroVADSession:
        if sample_rate != 16_000:
            raise ValueError("Silero VAD requires 16 kHz PCM")
        config = self._sherpa.VadModelConfig()
        config.silero_vad.model = str(self._model_path)
        config.silero_vad.threshold = self._threshold
        config.silero_vad.min_silence_duration = self._min_silence_seconds
        config.silero_vad.min_speech_duration = self._min_speech_seconds
        config.sample_rate = sample_rate
        config.num_threads = 1
        detector = self._sherpa.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        return SileroVADSession(detector, int(config.silero_vad.window_size))


class SileroVADSession:
    def __init__(self, detector: Any, window_size: int) -> None:
        self._detector = detector
        self._window_size = window_size
        self._pending = np.empty(0, dtype=np.float32)
        self._started = False
        self._closed = False

    def accept_pcm(self, pcm_s16le: bytes) -> VADResult:
        if self._closed:
            raise RuntimeError("VAD session is closed")
        if len(pcm_s16le) % 2:
            raise ValueError("VAD PCM must contain complete int16 samples")
        samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
        self._pending = np.concatenate((self._pending, samples))
        started_now = False
        ended = False
        while self._pending.size >= self._window_size:
            window = self._pending[: self._window_size]
            self._pending = self._pending[self._window_size :]
            self._detector.accept_waveform(window)
            if self._detector.is_speech_detected and not self._started:
                self._started = True
                started_now = True
            while not self._detector.empty():
                self._detector.pop()
                ended = self._started
                self._started = False
        return VADResult(started_now, ended)

    def cancel(self) -> None:
        if not self._closed:
            self._detector.reset()
            self._closed = True


def build_silero_vad_engine(model_dir: Path) -> SileroVADEngine:
    try:
        root = model_dir.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"VAD model directory is unavailable: {model_dir}") from exc
    model = _verified(root, "silero-vad-v5.onnx", _MODEL_SHA256)
    _verified(root, "LICENSE", _LICENSE_SHA256)
    return SileroVADEngine(model)


def _verified(root: Path, name: str, expected: str) -> Path:
    candidate = (root / name).resolve()
    if not candidate.is_file() or not candidate.is_relative_to(root):
        raise RuntimeError(f"VAD model artifact is missing: {name}")
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise RuntimeError(f"VAD model checksum mismatch: {name}")
    return candidate


__all__ = [
    "SileroVADEngine",
    "SileroVADSession",
    "VADEngine",
    "VADResult",
    "VADSession",
    "build_silero_vad_engine",
]
