"""Bounded jitter-buffer и lifecycle входящего TTS stream."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from voice_client.net.protocol import ClientProtocolError


@dataclass(frozen=True, slots=True)
class AudioOutputFormat:
    sample_rate: int = 24_000
    channels: int = 1
    sample_width: int = 2

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.channels * self.sample_width

    @classmethod
    def from_tts_start(cls, message: dict[str, Any]) -> tuple[str, AudioOutputFormat]:
        if message.get("type") != "tts_start":
            raise ClientProtocolError("expected tts_start")
        stream_id = message.get("stream_id")
        raw_format = message.get("format")
        if not isinstance(stream_id, str) or not stream_id or len(stream_id) > 128:
            raise ClientProtocolError("invalid TTS stream_id")
        if not isinstance(raw_format, dict):
            raise ClientProtocolError("invalid TTS format")
        values = tuple(raw_format.get(key) for key in ("sample_rate", "channels", "sample_width"))
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ClientProtocolError("invalid TTS format")
        audio_format = cls(
            sample_rate=int(values[0]),
            channels=int(values[1]),
            sample_width=int(values[2]),
        )
        if audio_format != cls():
            raise ClientProtocolError("protocol v1 requires PCM S16LE mono 24 kHz")
        return stream_id, audio_format


class TTSPlaybackBuffer:
    """FIFO для упорядоченного WebSocket PCM с bounded latency."""

    def __init__(self, *, min_ms: int = 50, max_ms: int = 150) -> None:
        if not 0 <= min_ms <= max_ms <= 2_000:
            raise ValueError("jitter bounds must satisfy 0 <= min <= max <= 2000 ms")
        self.min_ms = min_ms
        self.max_ms = max_ms
        self.stream_id: str | None = None
        self.audio_format = AudioOutputFormat()
        self.ending = False
        self.primed = False
        self.dropped_bytes = 0
        self._chunks: deque[bytes] = deque()
        self._buffered_bytes = 0

    @property
    def buffered_ms(self) -> float:
        return self._buffered_bytes * 1000 / self.audio_format.bytes_per_second

    def start(self, message: dict[str, Any]) -> None:
        stream_id, audio_format = AudioOutputFormat.from_tts_start(message)
        self.clear()
        self.stream_id = stream_id
        self.audio_format = audio_format

    def push(self, pcm_s16le: bytes) -> None:
        if self.stream_id is None or self.ending:
            raise ClientProtocolError("TTS binary chunk is not expected")
        frame_bytes = self.audio_format.channels * self.audio_format.sample_width
        if not pcm_s16le or len(pcm_s16le) % frame_bytes:
            raise ClientProtocolError("invalid TTS PCM chunk")
        self._chunks.append(bytes(pcm_s16le))
        self._buffered_bytes += len(pcm_s16le)
        max_bytes = self.audio_format.bytes_per_second * self.max_ms // 1000
        while self._buffered_bytes > max_bytes and len(self._chunks) > 1:
            dropped = self._chunks.popleft()
            self._buffered_bytes -= len(dropped)
            self.dropped_bytes += len(dropped)

    def pop(self) -> bytes | None:
        if not self._chunks:
            return None
        if not self.primed:
            if not self.ending and self.buffered_ms < self.min_ms:
                return None
            self.primed = True
        chunk = self._chunks.popleft()
        self._buffered_bytes -= len(chunk)
        return chunk

    def finish(self, message: dict[str, Any]) -> None:
        if message.get("type") != "tts_end" or message.get("stream_id") != self.stream_id:
            raise ClientProtocolError("stale TTS end")
        self.ending = True

    def flush(self) -> bytes:
        payload = b"".join(self._chunks)
        self.clear()
        return payload

    def barge_in(self) -> dict[str, str]:
        """Сначала очищает playback, затем возвращает wire interrupt."""

        self.clear()
        return {"type": "interrupt"}

    def clear(self) -> None:
        self._chunks.clear()
        self._buffered_bytes = 0
        self.stream_id = None
        self.ending = False
        self.primed = False
        self.dropped_bytes = 0
