"""Координатор клиентского voice turn без зависимости от UI toolkit."""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from voice_client.net.protocol import MAX_SEQUENCE, SpeechLanguage


class VoiceTransport(Protocol):
    def set_language(self, language: SpeechLanguage) -> None: ...

    def begin_audio(self, seq: int) -> None: ...

    def send_audio(self, pcm_s16le: bytes) -> None: ...

    def end_audio(self) -> None: ...

    def interrupt(self) -> None: ...


class PipelineState(str, Enum):
    DISCONNECTED = "disconnected"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    TALKING = "talking"
    MUTED = "muted"


class VoicePipeline:
    """Связывает capture, transport и server events с явным выбором ru/en."""

    def __init__(
        self,
        transport: VoiceTransport,
        *,
        language: SpeechLanguage = "ru",
        initial_sequence: int = 1,
    ) -> None:
        if not 1 <= initial_sequence <= MAX_SEQUENCE:
            raise ValueError("initial_sequence must be a positive uint64")
        self.transport = transport
        self.language: SpeechLanguage = language
        self.state = PipelineState.DISCONNECTED
        self._next_sequence = initial_sequence
        self.active_sequence: int | None = None
        self.transport.set_language(language)

    def set_connected(self, connected: bool) -> None:
        self.active_sequence = None
        self.state = PipelineState.IDLE if connected else PipelineState.DISCONNECTED

    def set_language(self, language: SpeechLanguage) -> None:
        """Настройка UI: никакой автоматической детекции языка нет."""

        self.transport.set_language(language)
        self.language = language

    def start_utterance(self) -> int:
        if self.state is PipelineState.DISCONNECTED:
            raise RuntimeError("voice pipeline is disconnected")
        if self.state is PipelineState.MUTED:
            raise RuntimeError("voice pipeline is muted")
        if self.state in {PipelineState.THINKING, PipelineState.TALKING}:
            # VoiceWSClient очищает playback синхронно до отправки interrupt.
            self.transport.interrupt()
        seq = self._next_sequence
        self._next_sequence = 1 if seq == MAX_SEQUENCE else seq + 1
        self.transport.begin_audio(seq)
        self.active_sequence = seq
        self.state = PipelineState.LISTENING
        return seq

    def push_audio(self, pcm_s16le: bytes) -> None:
        if self.state is not PipelineState.LISTENING:
            return
        self.transport.send_audio(pcm_s16le)

    def finish_utterance(self) -> None:
        if self.state is not PipelineState.LISTENING:
            return
        self.transport.end_audio()
        self.state = PipelineState.THINKING

    def set_muted(self, muted: bool) -> None:
        if muted:
            active_states = {
                PipelineState.LISTENING,
                PipelineState.THINKING,
                PipelineState.TALKING,
            }
            if self.state in active_states:
                self.transport.interrupt()
            self.active_sequence = None
            self.state = PipelineState.MUTED
        elif self.state is PipelineState.MUTED:
            self.state = PipelineState.IDLE

    def on_server_event(self, message: dict[str, Any]) -> None:
        frame_type = message.get("type")
        if frame_type == "final":
            self.active_sequence = None
            self.state = PipelineState.THINKING
        elif frame_type == "tts_start":
            self.state = PipelineState.TALKING
        elif frame_type == "tts_end":
            self.state = PipelineState.IDLE
        elif frame_type == "error" and self.state is not PipelineState.MUTED:
            self.active_sequence = None
            self.state = PipelineState.IDLE


__all__ = ["PipelineState", "VoicePipeline", "VoiceTransport"]
