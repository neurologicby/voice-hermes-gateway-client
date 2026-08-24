from __future__ import annotations

import pytest

from voice_client.net.protocol import (
    ClientAudioTurn,
    ClientProtocolError,
    audio_end,
    audio_start,
    decode_audio_chunk,
    encode_audio_chunk,
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
