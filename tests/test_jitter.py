from __future__ import annotations

import pytest

from voice_client.audio.jitter import AudioOutputFormat, TTSPlaybackBuffer
from voice_client.net.protocol import ClientProtocolError


def _start(stream_id: str = "stream-1") -> dict[str, object]:
    return {
        "type": "tts_start",
        "stream_id": stream_id,
        "format": {"sample_rate": 24_000, "channels": 1, "sample_width": 2},
    }


def test_jitter_primes_then_drains_in_websocket_order() -> None:
    jitter = TTSPlaybackBuffer(min_ms=50, max_ms=150)
    jitter.start(_start())
    chunk_40ms = b"\x01\x00" * 960
    jitter.push(chunk_40ms)
    assert jitter.pop() is None
    jitter.push(b"\x02\x00" * 480)
    assert jitter.pop() == chunk_40ms
    assert jitter.pop() == b"\x02\x00" * 480


def test_jitter_is_bounded_and_drops_oldest_audio() -> None:
    jitter = TTSPlaybackBuffer(min_ms=0, max_ms=100)
    jitter.start(_start())
    chunks = [bytes([index, 0]) * 1_200 for index in range(3)]
    for chunk in chunks:
        jitter.push(chunk)
    assert jitter.buffered_ms <= 100
    assert jitter.dropped_bytes == len(chunks[0])
    assert jitter.pop() == chunks[1]


def test_barge_in_clears_audio_before_interrupt() -> None:
    jitter = TTSPlaybackBuffer(min_ms=0)
    jitter.start(_start())
    jitter.push(b"\x01\x00")
    assert jitter.barge_in() == {"type": "interrupt"}
    assert jitter.stream_id is None
    assert jitter.pop() is None
    with pytest.raises(ClientProtocolError):
        jitter.push(b"\x01\x00")


def test_stale_end_and_wrong_format_are_rejected() -> None:
    jitter = TTSPlaybackBuffer()
    jitter.start(_start())
    with pytest.raises(ClientProtocolError):
        jitter.finish({"type": "tts_end", "stream_id": "old"})
    wrong = _start("stream-2")
    wrong["format"] = {"sample_rate": 22_050, "channels": 1, "sample_width": 2}
    with pytest.raises(ClientProtocolError):
        AudioOutputFormat.from_tts_start(wrong)


def test_tts_end_releases_short_tail_and_flush_resets() -> None:
    jitter = TTSPlaybackBuffer(min_ms=50)
    jitter.start(_start())
    tail = b"\x03\x00" * 240
    jitter.push(tail)
    jitter.finish({"type": "tts_end", "stream_id": "stream-1"})
    assert jitter.pop() == tail
    assert jitter.flush() == b""
    assert jitter.stream_id is None
