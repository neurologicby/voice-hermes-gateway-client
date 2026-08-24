from __future__ import annotations

import threading
from typing import Any

import numpy as np

from voice_client.audio.recorder import MicrophoneRecorder, PCM16Resampler


class FakeInputStream:
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def feed(self, pcm: bytes) -> None:
        self.callback(pcm, len(pcm) // 2, None, None)


def test_resampler_48k_to_16k_averages_triplets_and_keeps_remainder() -> None:
    resampler = PCM16Resampler()
    first = resampler.process(np.array([0, 3, 6, 9], dtype="<i2").tobytes())
    assert np.frombuffer(first, "<i2").tolist() == [3]
    output = resampler.process(np.array([12, 15], dtype="<i2").tobytes())
    assert np.frombuffer(output, "<i2").tolist() == [12]


def test_recorder_moves_resampling_out_of_portaudio_callback() -> None:
    streams: list[FakeInputStream] = []
    options: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeInputStream:
        options.update(kwargs)
        stream = FakeInputStream(kwargs["callback"])
        streams.append(stream)
        return stream

    received: list[bytes] = []
    delivered = threading.Event()

    def on_pcm(pcm: bytes) -> None:
        received.append(pcm)
        delivered.set()

    recorder = MicrophoneRecorder(stream_factory=factory)
    recorder.start(on_pcm)
    input_pcm = np.arange(1_440, dtype="<i2").tobytes()
    streams[0].feed(input_pcm)
    assert delivered.wait(1)
    assert len(received[0]) == 480 * 2
    assert options["samplerate"] == 48_000
    assert options["blocksize"] == 1_440
    recorder.stop()
    assert streams[0].stopped and streams[0].closed
