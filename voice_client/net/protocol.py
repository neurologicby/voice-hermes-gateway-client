"""Независимая клиентская реализация VoiceGateway protocol v1."""

from __future__ import annotations

from typing import Literal

PROTOCOL_VERSION = 1
AUDIO_SEQUENCE_BYTES = 8
MAX_SEQUENCE = (1 << (AUDIO_SEQUENCE_BYTES * 8)) - 1


class ClientProtocolError(ValueError):
    """Ошибка локального формирования wire frame."""


class ClientAudioTurn:
    """Состояние исходящей реплики с поддержкой server-side VAD endpoint."""

    def __init__(self) -> None:
        self.active_seq: int | None = None
        self.end_requested = False
        self.completed_seq: int | None = None

    def begin(
        self, seq: int, language: Literal["auto", "ru", "en"] = "auto"
    ) -> dict[str, object]:
        if self.active_seq is not None:
            raise ClientProtocolError("audio turn is already active")
        frame = audio_start(seq, language)
        self.active_seq = seq
        self.end_requested = False
        return frame

    def chunk(self, pcm_s16le: bytes) -> bytes:
        if self.active_seq is None or self.end_requested:
            raise ClientProtocolError("audio chunks are not accepted in current state")
        return encode_audio_chunk(self.active_seq, pcm_s16le)

    def request_end(self, *, vad: str = "speech") -> dict[str, object] | None:
        if self.active_seq is None:
            return None
        if self.end_requested:
            return None
        self.end_requested = True
        return audio_end(self.active_seq, vad=vad)

    def server_vad_endpoint(self, seq: int) -> dict[str, object] | None:
        if self.active_seq != seq:
            raise ClientProtocolError("stale server VAD endpoint")
        return self.request_end(vad="server_silence")

    def accept_final(self, seq: int) -> None:
        if self.active_seq != seq:
            raise ClientProtocolError("stale final transcript")
        self.completed_seq = seq
        self.active_seq = None
        self.end_requested = False


def audio_start(seq: int, language: Literal["auto", "ru", "en"] = "auto") -> dict[str, object]:
    _validate_sequence(seq)
    if language not in {"auto", "ru", "en"}:
        raise ClientProtocolError("unsupported STT language")
    return {"type": "audio_start", "seq": seq, "lang": language}


def audio_end(seq: int, *, vad: str = "speech") -> dict[str, object]:
    _validate_sequence(seq)
    return {"type": "audio_end", "seq": seq, "vad": vad}


def encode_audio_chunk(seq: int, pcm_s16le: bytes) -> bytes:
    """Добавляет к PCM обязательный uint64 big-endian sequence header."""

    _validate_sequence(seq)
    if not pcm_s16le or len(pcm_s16le) % 2:
        raise ClientProtocolError("PCM S16LE chunk must contain complete samples")
    return seq.to_bytes(AUDIO_SEQUENCE_BYTES, "big", signed=False) + pcm_s16le


def decode_audio_chunk(frame: bytes) -> tuple[int, bytes]:
    """Тестовый/диагностический decoder симметричного audio frame."""

    if len(frame) <= AUDIO_SEQUENCE_BYTES:
        raise ClientProtocolError("audio frame has no PCM payload")
    seq = int.from_bytes(frame[:AUDIO_SEQUENCE_BYTES], "big", signed=False)
    pcm = frame[AUDIO_SEQUENCE_BYTES:]
    _validate_sequence(seq)
    if len(pcm) % 2:
        raise ClientProtocolError("PCM S16LE chunk must contain complete samples")
    return seq, pcm


def _validate_sequence(seq: int) -> None:
    if isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= MAX_SEQUENCE:
        raise ClientProtocolError("sequence must be uint64 greater than zero")
