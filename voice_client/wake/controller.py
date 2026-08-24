"""State machine непрерывного локального wake-word контура."""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum
from typing import Any, Protocol

from voice_client.audio.vad import VADEngine, VADSession
from voice_client.net.protocol import MAX_SEQUENCE

from .base import WakeWordEngine


class WakeTransport(Protocol):
    def begin_audio(self, seq: int) -> None: ...

    def send_audio(self, pcm_s16le: bytes) -> None: ...

    def end_audio(self) -> None: ...

    def interrupt(self) -> None: ...


class WakeState(str, Enum):
    DISCONNECTED = "disconnected"
    SLEEP = "sleep"
    LISTENING = "listening"
    THINKING = "thinking"
    TALKING = "talking"
    MUTED = "muted"
    PAUSED = "paused"


class WakeController:
    """Не отправляет ни одного PCM-чанка до локального trigger."""

    def __init__(
        self,
        engine: WakeWordEngine,
        transport: WakeTransport,
        *,
        initial_sequence: int = 1,
        vad_engine: VADEngine | None = None,
        rearm_delay_seconds: float = 0.75,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= initial_sequence <= MAX_SEQUENCE:
            raise ValueError("initial_sequence must be a positive uint64")
        if rearm_delay_seconds < 0:
            raise ValueError("rearm_delay_seconds must not be negative")
        self.engine = engine
        self.transport = transport
        self.state = WakeState.DISCONNECTED
        self._next_sequence = initial_sequence
        self._vad_engine = vad_engine
        self._vad_session: VADSession | None = None
        self._rearm_delay_seconds = rearm_delay_seconds
        self._clock = clock
        self._rearm_at = 0.0

    def set_connected(self, connected: bool) -> None:
        self._reset_vad()
        self.engine.reset()
        self._rearm_at = 0.0
        self.state = WakeState.SLEEP if connected else WakeState.DISCONNECTED

    def process_pcm(self, pcm_s16le: bytes) -> bool:
        """Обработать mic chunk; True означает новый локальный trigger."""

        if self.state is WakeState.SLEEP:
            if self._clock() < self._rearm_at:
                return False
            if not self.engine.process(pcm_s16le):
                return False
            seq = self._next_sequence
            self._next_sequence = 1 if seq == MAX_SEQUENCE else seq + 1
            if self._vad_engine is not None:
                self._vad_session = self._vad_engine.create_session(sample_rate=16_000)
            self.transport.begin_audio(seq)
            self.state = WakeState.LISTENING
            return True
        if self.state is WakeState.LISTENING:
            self.transport.send_audio(pcm_s16le)
            if self._vad_session is not None:
                result = self._vad_session.accept_pcm(pcm_s16le)
                if result.speech_ended:
                    self.finish_utterance()
        return False

    def finish_utterance(self) -> None:
        if self.state is WakeState.LISTENING:
            self.transport.end_audio()
            self._reset_vad()
            self.state = WakeState.THINKING

    def set_muted(self, on: bool) -> None:
        if on:
            self._stop_active_turn()
            self.state = WakeState.MUTED
        elif self.state is WakeState.MUTED:
            self.engine.reset()
            self.state = WakeState.SLEEP

    def set_paused(self, on: bool) -> None:
        if on:
            self._stop_active_turn()
            self.state = WakeState.PAUSED
        elif self.state is WakeState.PAUSED:
            self.engine.reset()
            self.state = WakeState.SLEEP

    def on_server_event(self, message: dict[str, Any]) -> None:
        frame_type = message.get("type")
        if frame_type == "final" and self.state is WakeState.LISTENING:
            self._reset_vad()
            self.state = WakeState.THINKING
        elif frame_type == "tts_start" and self.state not in {
            WakeState.MUTED,
            WakeState.PAUSED,
        }:
            self.state = WakeState.TALKING
        elif frame_type in {"tts_end", "error"} and self.state not in {
            WakeState.MUTED,
            WakeState.PAUSED,
            WakeState.DISCONNECTED,
        }:
            self.engine.reset()
            self._reset_vad()
            self._rearm_at = self._clock() + self._rearm_delay_seconds
            self.state = WakeState.SLEEP

    def close(self) -> None:
        self._reset_vad()
        self.engine.close()

    def _stop_active_turn(self) -> None:
        if self.state in {WakeState.LISTENING, WakeState.THINKING, WakeState.TALKING}:
            self.transport.interrupt()
        self._reset_vad()
        self.engine.reset()

    def _reset_vad(self) -> None:
        if self._vad_session is not None:
            self._vad_session.cancel()
            self._vad_session = None


__all__ = ["WakeController", "WakeState", "WakeTransport"]
