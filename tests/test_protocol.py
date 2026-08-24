from __future__ import annotations

import pytest

from voice_client.net.protocol import (
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
