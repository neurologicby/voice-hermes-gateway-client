"""Checksum-verified offline Sherpa recognizers for the wake phrase engine."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

from voice_client.net.protocol import SpeechLanguage

from .sherpa_phrase import SherpaPhraseWakeEngine

_BUNDLES: dict[SpeechLanguage, tuple[int, dict[str, str]]] = {
    "ru": (
        8_000,
        {
            "model.onnx": "5ded080e2a6c86ecc11bcb0902d77524eb3e8b0844cb0c0754347f5aafb4dabc",
            "tokens.txt": "27f7b3ba2096c572375fba1a6b29af1f80d86e08a329940612908112695f97e0",
            "LICENSE": "c8e6ecb86af681d9815d1ada7b1a7780ea5e5cb68a5df94fc800adab1a6ce027",
        },
    ),
    "en": (
        16_000,
        {
            "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx": (
                "563fde436d16cf7607cf408cd6b30909819d03162652ef389c2450ced3f45ac1"
            ),
            "decoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx": (
                "98da299f471e38bb4e1a8df579b8cc9122d6039576a77e357b3c60f17dd83b02"
            ),
            "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx": (
                "d944208d660d67c8d72cd2acaeac971fa5ceb8c80e76c1968148846fedd6e297"
            ),
            "tokens.txt": "49e3c2646595fd907228b3c6787069658f67b17377c60aeb8619c4551b2316fb",
            "LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        },
    ),
}


def build_sherpa_phrase_engine(
    language: SpeechLanguage,
    phrase: str,
    model_dir: Path,
    *,
    sherpa_module: Any | None = None,
) -> SherpaPhraseWakeEngine:
    sample_rate, files = _verify_bundle(language, model_dir)
    sherpa = sherpa_module or importlib.import_module("sherpa_onnx")
    tokens = model_dir / "tokens.txt"
    if language == "ru":
        recognizer = sherpa.OnlineRecognizer.from_t_one_ctc(
            tokens=str(tokens),
            model=str(model_dir / "model.onnx"),
            num_threads=1,
            sample_rate=sample_rate,
            enable_endpoint_detection=False,
        )
    else:
        recognizer = sherpa.OnlineRecognizer.from_transducer(
            tokens=str(tokens),
            encoder=str(model_dir / next(name for name in files if name.startswith("encoder"))),
            decoder=str(model_dir / next(name for name in files if name.startswith("decoder"))),
            joiner=str(model_dir / next(name for name in files if name.startswith("joiner"))),
            num_threads=1,
            sample_rate=sample_rate,
            enable_endpoint_detection=False,
        )
    return SherpaPhraseWakeEngine(
        phrase,
        recognizer,
        model_sample_rate=sample_rate,
    )


def _verify_bundle(
    language: SpeechLanguage, model_dir: Path
) -> tuple[int, dict[str, str]]:
    sample_rate, expected = _BUNDLES[language]
    try:
        root = model_dir.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"Wake model directory is unavailable: {model_dir}") from exc
    for name, digest in expected.items():
        candidate = (root / name).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(root):
            raise RuntimeError(f"Wake model artifact is missing: {name}")
        if _sha256(candidate) != digest:
            raise RuntimeError(f"Wake model checksum mismatch: {name}")
    return sample_rate, expected


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ["build_sherpa_phrase_engine"]
