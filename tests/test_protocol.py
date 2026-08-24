from __future__ import annotations

import pytest

from voice_client.net.protocol import (
    ClientAudioTurn,
    ClientProtocolError,
    STTLatencyMetrics,
    audio_end,
    audio_start,
    decode_audio_chunk,
    encode_audio_chunk,
    hello,
    pair_request,
)


def test_audio_control_and_binary_frames_share_sequence() -> None:
    assert audio_start(7, "ru") == {"type": "audio_start", "seq": 7, "lang": "ru"}
    frame = encode_audio_chunk(7, b"\x01\x00\x02\x00")
    assert decode_audio_chunk(frame) == (7, b"\x01\x00\x02\x00")
    assert audio_end(7) == {"type": "audio_end", "seq": 7, "vad": "speech"}


@pytest.mark.parametrize("seq", [0, -1, True, 1 << 64])
def test_invalid_sequence_is_rejected(seq: int) -> None:
    with pytest.raises(ClientProtocolError):
        encode_audio_chunk(seq, b"\x00\x00")


@pytest.mark.parametrize("pcm", [b"", b"\x00"])
def test_invalid_pcm_chunk_is_rejected(pcm: bytes) -> None:
    with pytest.raises(ClientProtocolError):
        encode_audio_chunk(1, pcm)


def test_server_vad_endpoint_stops_chunks_and_requests_audio_end() -> None:
    turn = ClientAudioTurn()
    assert turn.begin(7, "ru")["type"] == "audio_start"
    assert turn.chunk(b"\x01\x00").endswith(b"\x01\x00")
    assert turn.server_vad_endpoint(7) == {
        "type": "audio_end",
        "seq": 7,
        "vad": "server_silence",
    }
    assert turn.server_vad_endpoint(7) is None
    with pytest.raises(ClientProtocolError):
        turn.chunk(b"\x01\x00")
    turn.accept_final(7)
    assert turn.completed_seq == 7


def test_audio_turn_rejects_stale_server_events() -> None:
    turn = ClientAudioTurn()
    turn.begin(7)
    with pytest.raises(ClientProtocolError):
        turn.server_vad_endpoint(6)
    with pytest.raises(ClientProtocolError):
        turn.accept_final(6)


def test_language_switch_is_persisted_for_next_audio_turn() -> None:
    turn = ClientAudioTurn()
    assert turn.begin(1)["lang"] == "ru"
    turn.accept_final(1)
    turn.set_language("en")
    assert turn.begin(2)["lang"] == "en"


def test_auto_language_is_not_supported() -> None:
    with pytest.raises(ClientProtocolError):
        audio_start(1, "auto")  # type: ignore[arg-type]


def test_optional_final_metrics_are_parsed() -> None:
    metrics = STTLatencyMetrics.from_final(
        {
            "type": "final",
            "metrics": {
                "queue_wait_ms": 1.25,
                "max_queue_wait_ms": 1,
                "first_interim_ms": 145.2,
                "finalization_ms": 18.4,
                "chunks": 12,
            },
        }
    )
    assert metrics == STTLatencyMetrics(1.25, 1.0, 145.2, 18.4, 12)
    assert STTLatencyMetrics.from_final({"type": "final"}) is None


def test_invalid_final_metrics_are_rejected() -> None:
    with pytest.raises(ClientProtocolError):
        STTLatencyMetrics.from_final({"metrics": {"queue_wait_ms": -1}})


def test_handshake_frames_validate_identity() -> None:
    device_id = "9f5e5b18-0d07-47da-8ed4-4c3a67dd535e"
    assert hello(device_id, "dmitry") == {
        "type": "hello",
        "proto": 1,
        "device_id": device_id,
        "user": "dmitry",
        "client": "voice-client/0.1",
    }
    assert pair_request(device_id, "Иванов Иван")["type"] == "pair_request"
    with pytest.raises(ClientProtocolError):
        hello("not-a-uuid", "dmitry")


def test_interrupt_resets_active_audio_turn() -> None:
    turn = ClientAudioTurn()
    turn.begin(3)
    turn.interrupt()
    assert turn.active_seq is None
    assert turn.begin(4, "en")["lang"] == "en"
