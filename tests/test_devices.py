from __future__ import annotations

from typing import Any

import sounddevice  # type: ignore[import-untyped]

from voice_client.audio.devices import query_audio_devices


def test_audio_device_query_normalizes_portaudio_records(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        sounddevice,
        "query_devices",
        lambda: [
            {"name": "Microphone", "max_input_channels": 1, "max_output_channels": 0},
            {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2},
        ],
    )
    devices = query_audio_devices()
    assert devices[0].index == 0
    assert devices[0].input_channels == 1
    assert devices[1].name == "Speakers"
    assert devices[1].output_channels == 2
