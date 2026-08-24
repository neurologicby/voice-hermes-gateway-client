"""Локальные wake-word движки клиента."""

from .base import WakeWordEngine, normalize_phrase
from .factory import build_sherpa_phrase_engine
from .loader import WakeEngineLoader
from .sherpa_phrase import SherpaPhraseWakeEngine

__all__ = [
    "SherpaPhraseWakeEngine",
    "WakeEngineLoader",
    "WakeWordEngine",
    "build_sherpa_phrase_engine",
    "normalize_phrase",
]
