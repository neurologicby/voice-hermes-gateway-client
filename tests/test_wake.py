from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from voice_client.audio.vad import VADResult
from voice_client.wake import SherpaPhraseWakeEngine, WakeWordEngine
from voice_client.wake.controller import WakeController, WakeState


@dataclass
class Result:
    text: str


class FakeStream:
    def __init__(self) -> None:
        self.samples: list[Any] = []

    def accept_waveform(self, _sample_rate: int, samples: Any) -> None:
        self.samples.append(samples)


class FakeRecognizer:
    def __init__(self) -> None:
        self.text = ""
        self.streams: list[FakeStream] = []
        self.string_result = False

    def create_stream(self) -> FakeStream:
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, _stream: FakeStream) -> bool:
        return False

    def decode_stream(self, _stream: FakeStream) -> None:
        raise AssertionError("not ready")

    def get_result(self, _stream: FakeStream) -> Result | str:
        return self.text if self.string_result else Result(self.text)


class FakeWake(WakeWordEngine):
    def __init__(self) -> None:
        super().__init__("привет гермес")
        self.trigger = False
        self.reset_count = 0

    def process(self, _pcm_s16le: bytes) -> bool:
        value = self.trigger
        self.trigger = False
        return value

    def reset(self) -> None:
        self.reset_count += 1


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def begin_audio(self, seq: int) -> None:
        self.calls.append(("begin", seq))

    def send_audio(self, pcm_s16le: bytes) -> None:
        self.calls.append(("audio", pcm_s16le))

    def end_audio(self) -> None:
        self.calls.append(("end", None))

    def interrupt(self) -> None:
        self.calls.append(("interrupt", None))


class FakeVADSession:
    def __init__(self) -> None:
        self.results: list[VADResult] = []
        self.cancelled = False

    def accept_pcm(self, _pcm_s16le: bytes) -> VADResult:
        return self.results.pop(0) if self.results else VADResult()

    def cancel(self) -> None:
        self.cancelled = True


class FakeVADEngine:
    def __init__(self, session: FakeVADSession) -> None:
        self.session = session

    def create_session(self, *, sample_rate: int) -> FakeVADSession:
        assert sample_rate == 16_000
        return self.session


def test_sherpa_phrase_matches_words_and_resets_decoder() -> None:
    recognizer = FakeRecognizer()
    engine = SherpaPhraseWakeEngine("Hello, Hermes!", recognizer)
    pcm = np.arange(320, dtype="<i2").tobytes()
    recognizer.text = "well hello hermes please"
    assert engine.process(pcm)
    assert len(recognizer.streams) == 2
    recognizer.text = "hermesian"
    assert not engine.process(pcm)

    recognizer.string_result = True
    recognizer.text = "hello hermes"
    assert engine.process(pcm)


def test_background_audio_never_reaches_transport_before_trigger() -> None:
    engine = FakeWake()
    transport = FakeTransport()
    controller = WakeController(engine, transport)
    controller.set_connected(True)
    assert not controller.process_pcm(b"background")
    assert transport.calls == []
    engine.trigger = True
    assert controller.process_pcm(b"wake phrase")
    assert transport.calls == [("begin", 1)]
    controller.process_pcm(b"command")
    assert transport.calls[-1] == ("audio", b"command")


def test_mute_and_pause_block_detection_and_upload() -> None:
    engine = FakeWake()
    transport = FakeTransport()
    controller = WakeController(engine, transport)
    controller.set_connected(True)
    controller.set_muted(True)
    engine.trigger = True
    assert not controller.process_pcm(b"wake")
    assert transport.calls == []
    controller.set_muted(False)
    assert controller.process_pcm(b"wake")
    controller.set_paused(True)
    assert controller.state is WakeState.PAUSED
    assert transport.calls[-1] == ("interrupt", None)
    controller.process_pcm(b"must not upload")
    assert transport.calls[-1] == ("interrupt", None)


def test_russian_hands_free_turn_ends_after_vad_silence() -> None:
    engine = FakeWake()
    transport = FakeTransport()
    vad = FakeVADSession()
    vad.results = [VADResult(speech_started=True), VADResult(speech_ended=True)]
    controller = WakeController(engine, transport, vad_engine=FakeVADEngine(vad))
    controller.set_connected(True)
    engine.trigger = True
    controller.process_pcm(b"wake")
    speech = "русская речь".encode()
    silence = "тишина".encode()
    controller.process_pcm(speech)
    controller.process_pcm(silence)
    assert transport.calls == [
        ("begin", 1),
        ("audio", speech),
        ("audio", silence),
        ("end", None),
    ]
    assert controller.state is WakeState.THINKING
    assert vad.cancelled


def test_tts_tail_cannot_retrigger_russian_wake_phrase() -> None:
    now = [10.0]
    engine = FakeWake()
    transport = FakeTransport()
    controller = WakeController(engine, transport, clock=lambda: now[0])
    controller.set_connected(True)
    controller.on_server_event({"type": "tts_start"})
    controller.on_server_event({"type": "tts_end"})
    engine.trigger = True
    assert not controller.process_pcm(b"speaker tail")
    now[0] += 0.75
    assert controller.process_pcm(b"new user wake")
