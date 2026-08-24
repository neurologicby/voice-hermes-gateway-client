"""Run Russian/English wake phrase and Silero VAD against a local WAV."""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt

CLIENT_ROOT = Path(__file__).resolve().parents[1]
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from voice_client.audio.vad import build_silero_vad_engine  # noqa: E402
from voice_client.wake import build_sherpa_phrase_engine  # noqa: E402

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 480


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--language", choices=("ru", "en"), required=True)
    parser.add_argument("--phrase", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--vad-model-dir", type=Path, required=True)
    args = parser.parse_args()

    pcm = _load_pcm_16k(args.audio)
    load_started = time.perf_counter()
    wake = build_sherpa_phrase_engine(args.language, args.phrase, args.model_dir)
    vad = build_silero_vad_engine(args.vad_model_dir)
    load_ms = round((time.perf_counter() - load_started) * 1_000)

    trigger_ms: int | None = None
    decode_started = time.perf_counter()
    for offset in range(0, pcm.size, CHUNK_SAMPLES):
        if wake.process(pcm[offset : offset + CHUNK_SAMPLES].astype("<i2").tobytes()):
            trigger_ms = round((offset + CHUNK_SAMPLES) / SAMPLE_RATE * 1_000)
            break
    decode_ms = round((time.perf_counter() - decode_started) * 1_000)
    wake.close()

    session = vad.create_session(sample_rate=SAMPLE_RATE)
    speech_start_ms: int | None = None
    speech_end_ms: int | None = None
    padded = np.concatenate((pcm, np.zeros(SAMPLE_RATE, dtype=np.int16)))
    for offset in range(0, padded.size, CHUNK_SAMPLES):
        result = session.accept_pcm(
            padded[offset : offset + CHUNK_SAMPLES].astype("<i2").tobytes()
        )
        elapsed = round((offset + CHUNK_SAMPLES) / SAMPLE_RATE * 1_000)
        if result.speech_started and speech_start_ms is None:
            speech_start_ms = elapsed
        if result.speech_ended:
            speech_end_ms = elapsed
            break
    session.cancel()

    print(
        json.dumps(
            {
                "audio": args.audio.name,
                "language": args.language,
                "phrase": args.phrase,
                "audio_ms": round(pcm.size / SAMPLE_RATE * 1_000),
                "load_ms": load_ms,
                "decode_ms": decode_ms,
                "trigger_ms": trigger_ms,
                "speech_start_ms": speech_start_ms,
                "speech_end_ms": speech_end_ms,
            },
            ensure_ascii=False,
        )
    )
    return 0 if trigger_ms is not None and speech_end_ms is not None else 1


def _load_pcm_16k(path: Path) -> npt.NDArray[np.int16]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("probe requires mono PCM S16LE WAV")
        source_rate = source.getframerate()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    if source_rate == SAMPLE_RATE:
        return samples.copy()
    output_size = round(samples.size * SAMPLE_RATE / source_rate)
    positions = np.arange(output_size, dtype=np.float64) * source_rate / SAMPLE_RATE
    return np.interp(positions, np.arange(samples.size), samples).astype(np.int16)


if __name__ == "__main__":
    raise SystemExit(main())
