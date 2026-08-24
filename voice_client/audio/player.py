"""Потоковый PCM-плеер с bounded очередью и тестируемым backend."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from voice_client.audio.jitter import TTSPlaybackBuffer


class OutputStream(Protocol):
    def start(self) -> None: ...

    def write(self, data: bytes) -> bool: ...

    def abort(self) -> None: ...

    def close(self) -> None: ...


StreamFactory = Callable[..., OutputStream]


class _Kind(Enum):
    AUDIO = "audio"
    RESET = "reset"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class _Item:
    generation: int
    kind: _Kind
    pcm: bytes = b""


class AudioPlayer:
    """Не блокирует network/Qt threads и отбрасывает старый звук при barge-in."""

    def __init__(
        self,
        *,
        device: int | str | None = None,
        min_jitter_ms: int = 50,
        max_jitter_ms: int = 150,
        queue_limit: int = 16,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        if queue_limit < 2:
            raise ValueError("queue_limit must be at least 2")
        self.device = device
        self.jitter = TTSPlaybackBuffer(min_ms=min_jitter_ms, max_ms=max_jitter_ms)
        self._stream_factory = stream_factory or _sounddevice_stream
        self._queue: queue.Queue[_Item] = queue.Queue(queue_limit)
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._thread: threading.Thread | None = None
        self.dropped_chunks = 0

    def set_device(self, device: int | str | None) -> None:
        with self._lock:
            self._ensure_open()
            if device == self.device:
                return
            self.interrupt()
            self.device = device

    def start(self, message: dict[str, Any]) -> None:
        with self._lock:
            self._ensure_open()
            self.jitter.start(message)
            self._generation += 1
            self._ensure_thread()
            self._clear_queue()
            self._put_control(_Kind.RESET)

    def push(self, pcm_s16le: bytes) -> None:
        with self._lock:
            self._ensure_open()
            self.jitter.push(pcm_s16le)
            self._drain_jitter()

    def finish(self, message: dict[str, Any]) -> None:
        with self._lock:
            self._ensure_open()
            self.jitter.finish(message)
            self._drain_jitter()
            tail = self.jitter.flush()
            if tail:
                self._put_audio(tail)

    def interrupt(self) -> None:
        """Сначала инвалидирует PCM, затем просит worker оборвать PortAudio stream."""

        with self._lock:
            if self._closed:
                return
            self.jitter.clear()
            self._generation += 1
            self._clear_queue()
            if self._thread is not None:
                self._put_control(_Kind.RESET)

    def close(self, timeout: float = 2.0) -> None:
        with self._lock:
            if self._closed:
                return
            self.jitter.clear()
            self._generation += 1
            self._closed = True
            self._clear_queue()
            thread = self._thread
            if thread is not None:
                self._put_control(_Kind.CLOSE)
        if thread is not None:
            thread.join(timeout)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("audio player is closed")

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._worker,
            name="voice-audio-output",
            daemon=True,
        )
        self._thread.start()

    def _drain_jitter(self) -> None:
        while (chunk := self.jitter.pop()) is not None:
            self._put_audio(chunk)

    def _put_audio(self, pcm: bytes) -> None:
        item = _Item(self._generation, _Kind.AUDIO, pcm)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self.dropped_chunks += 1
            self._queue.put_nowait(item)

    def _put_control(self, kind: _Kind) -> None:
        item = _Item(self._generation, kind)
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

    def _worker(self) -> None:
        stream: OutputStream | None = None
        try:
            while True:
                item = self._queue.get()
                if item.kind is _Kind.CLOSE:
                    return
                if item.kind is _Kind.RESET:
                    _close_stream(stream)
                    stream = None
                    continue
                if item.generation != self._generation:
                    continue
                if stream is None:
                    stream = self._stream_factory(
                        samplerate=24_000,
                        channels=1,
                        dtype="int16",
                        device=self.device,
                        blocksize=0,
                        latency="low",
                    )
                    stream.start()
                stream.write(item.pcm)
        finally:
            _close_stream(stream)


def _close_stream(stream: OutputStream | None) -> None:
    if stream is not None:
        try:
            stream.abort()
        finally:
            stream.close()
def _sounddevice_stream(**kwargs: Any) -> OutputStream:
    try:
        import sounddevice  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - зависит от optional extra
        raise RuntimeError("install voice-gateway-client[audio] for playback") from exc
    return sounddevice.RawOutputStream(**kwargs)  # type: ignore[no-any-return]


__all__ = ["AudioPlayer", "OutputStream", "StreamFactory"]
