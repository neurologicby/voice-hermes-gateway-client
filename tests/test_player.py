from __future__ import annotations

import threading
from typing import Any

from voice_client.audio.player import AudioPlayer


def _start() -> dict[str, object]:
    return {
        "type": "tts_start",
        "stream_id": "stream-1",
        "format": {"sample_rate": 24_000, "channels": 1, "sample_width": 2},
    }


class FakeStream:
    def __init__(self) -> None:
        self.started = False
        self.writes: list[bytes] = []
        self.written = threading.Event()
        self.aborted = threading.Event()
        self.closed = False

    def start(self) -> None:
        self.started = True

    def write(self, data: bytes) -> bool:
        self.writes.append(data)
        self.written.set()
        return False

    def abort(self) -> None:
        self.aborted.set()

    def close(self) -> None:
        self.closed = True


def test_player_writes_pcm_off_caller_thread_and_interrupts() -> None:
    stream = FakeStream()
    options: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeStream:
        options.update(kwargs)
        return stream

    player = AudioPlayer(min_jitter_ms=50, stream_factory=factory)
    player.start(_start())
    pcm = b"\x01\x00" * 1_440  # 60 ms
    player.push(pcm)
    assert stream.written.wait(1)
    assert stream.writes == [pcm]
    assert options["samplerate"] == 24_000
    assert options["dtype"] == "int16"

    player.interrupt()
    assert stream.aborted.wait(1)
    assert stream.closed
    player.close()
