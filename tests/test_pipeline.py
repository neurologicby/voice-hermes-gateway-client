from __future__ import annotations

from voice_client.net.protocol import SpeechLanguage
from voice_client.pipeline import PipelineState, VoicePipeline


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def set_language(self, language: SpeechLanguage) -> None:
        self.calls.append(("language", language))

    def begin_audio(self, seq: int) -> None:
        self.calls.append(("begin", seq))

    def send_audio(self, pcm_s16le: bytes) -> None:
        self.calls.append(("audio", pcm_s16le))

    def end_audio(self) -> None:
        self.calls.append(("end", None))

    def interrupt(self) -> None:
        self.calls.append(("interrupt", None))


def test_explicit_language_is_applied_to_next_turn() -> None:
    transport = FakeTransport()
    pipeline = VoicePipeline(transport)
    pipeline.set_connected(True)
    pipeline.set_language("en")
    seq = pipeline.start_utterance()
    pipeline.push_audio(b"\x01\x00")
    pipeline.finish_utterance()
    assert seq == 1
    assert pipeline.language == "en"
    assert transport.calls == [
        ("language", "ru"),
        ("language", "en"),
        ("begin", 1),
        ("audio", b"\x01\x00"),
        ("end", None),
    ]
    assert pipeline.state is PipelineState.THINKING


def test_barge_in_interrupt_precedes_next_audio_start() -> None:
    transport = FakeTransport()
    pipeline = VoicePipeline(transport)
    pipeline.set_connected(True)
    pipeline.on_server_event({"type": "tts_start"})
    assert pipeline.state is PipelineState.TALKING
    pipeline.start_utterance()
    assert transport.calls[-2:] == [("interrupt", None), ("begin", 1)]
    assert pipeline.state.value == PipelineState.LISTENING.value


def test_server_events_drive_pipeline_state() -> None:
    pipeline = VoicePipeline(FakeTransport())
    pipeline.set_connected(True)
    pipeline.start_utterance()
    pipeline.on_server_event({"type": "final", "seq": 1, "text": "тест"})
    assert pipeline.state is PipelineState.THINKING
    pipeline.on_server_event({"type": "tts_start", "stream_id": "one"})
    assert pipeline.state.value == PipelineState.TALKING.value
    pipeline.on_server_event({"type": "tts_end", "stream_id": "one"})
    assert pipeline.state.value == PipelineState.IDLE.value
