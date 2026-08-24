"""Bounded microphone capture и преобразование PCM S16LE mono в 16 кГц."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt


class InputStream(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


InputStreamFactory = Callable[..., InputStream]
PCMCallback = Callable[[bytes], None]


class PCM16Resampler:
    """Stateful mono resampler; 48→16 кГц использует anti-alias box filter."""

    def __init__(self, input_rate: int = 48_000, output_rate: int = 16_000) -> None:
        if input_rate <= 0 or output_rate <= 0 or input_rate < output_rate:
            raise ValueError("resampler requires positive input_rate >= output_rate")
        self.input_rate = input_rate
        self.output_rate = output_rate
        self._remainder = np.empty(0, dtype=np.int16)

    def process(self, pcm_s16le: bytes) -> bytes:
        if len(pcm_s16le) % 2:
            raise ValueError("PCM S16LE input must contain complete samples")
        if not pcm_s16le:
            return b""
        samples = np.frombuffer(pcm_s16le, dtype="<i2")
        if self._remainder.size:
            samples = np.concatenate((self._remainder, samples))
        if self.input_rate % self.output_rate == 0:
            factor = self.input_rate // self.output_rate
            complete = samples.size // factor * factor
            self._remainder = samples[complete:].copy()
            if complete == 0:
                return b""
            grouped = samples[:complete].astype(np.int32).reshape(-1, factor)
            output = np.rint(grouped.mean(axis=1)).astype("<i2")
            return output.tobytes()
        return self._linear(samples)

    def reset(self) -> None:
        self._remainder = np.empty(0, dtype=np.int16)

    def _linear(self, samples: npt.NDArray[np.int16]) -> bytes:
        output_size = samples.size * self.output_rate // self.input_rate
        if output_size == 0:
            self._remainder = samples.copy()
            return b""
        consumed = output_size * self.input_rate // self.output_rate
        positions = np.arange(output_size, dtype=np.float64) * self.input_rate / self.output_rate
        output = np.interp(positions, np.arange(samples.size), samples).astype("<i2")
        self._remainder = samples[consumed:].copy()
        return output.tobytes()


class MicrophoneRecorder:
    """PortAudio callback только копирует bytes в bounded очередь."""

    _STOP = object()

    def __init__(
        self,
        *,
        device: int | str | None = None,
        input_rate: int = 48_000,
        output_rate: int = 16_000,
        block_ms: int = 30,
        queue_limit: int = 16,
        stream_factory: InputStreamFactory | None = None,
    ) -> None:
        if not 10 <= block_ms <= 1_000:
            raise ValueError("block_ms must be in range 10..1000")
        if queue_limit < 2:
            raise ValueError("queue_limit must be at least 2")
        self.device = device
        self.input_rate = input_rate
        self.block_ms = block_ms
        self.resampler = PCM16Resampler(input_rate, output_rate)
        self._stream_factory = stream_factory or _sounddevice_input_stream
        self._queue: queue.Queue[bytes | object] = queue.Queue(queue_limit)
        self._stream: InputStream | None = None
        self._thread: threading.Thread | None = None
        self._callback: PCMCallback | None = None
        self._running = False
        self.dropped_chunks = 0

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        try:
            import sounddevice  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError("install voice-gateway-client[audio] for capture") from exc
        return [dict(device) for device in sounddevice.query_devices()]

    def start(self, callback: PCMCallback) -> None:
        if self._running:
            raise RuntimeError("microphone recorder is already running")
        self._clear_queue()
        self.resampler.reset()
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(
            target=self._consume,
            name="voice-audio-input",
            daemon=True,
        )
        self._thread.start()
        try:
            self._stream = self._stream_factory(
                samplerate=self.input_rate,
                blocksize=self.input_rate * self.block_ms // 1_000,
                device=self.device,
                channels=1,
                dtype="int16",
                latency="low",
                callback=self._portaudio_callback,
            )
            self._stream.start()
        except Exception:
            self._running = False
            self._put_control(self._STOP)
            self._thread.join(1)
            self._thread = None
            self._callback = None
            raise

    def stop(self, timeout: float = 2.0) -> None:
        if not self._running:
            return
        self._running = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        self._put_control(self._STOP)
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None
        self._callback = None
        self._clear_queue()

    def _portaudio_callback(
        self,
        indata: Any,
        _frames: int,
        _time_info: Any,
        _status: Any,
    ) -> None:
        if not self._running:
            return
        chunk = bytes(indata)
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self.dropped_chunks += 1
            self._queue.put_nowait(chunk)

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._STOP:
                return
            callback = self._callback
            if callback is not None and isinstance(item, bytes):
                output = self.resampler.process(item)
                if output:
                    callback(output)

    def _put_control(self, item: object) -> None:
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    continue

    def _clear_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


def _sounddevice_input_stream(**kwargs: Any) -> InputStream:
    try:
        import sounddevice  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError("install voice-gateway-client[audio] for capture") from exc
    return sounddevice.RawInputStream(**kwargs)  # type: ignore[no-any-return]


__all__ = ["InputStream", "MicrophoneRecorder", "PCM16Resampler"]
