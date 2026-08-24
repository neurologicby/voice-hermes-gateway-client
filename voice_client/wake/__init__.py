"""Локальные wake-word движки клиента."""

from .base import WakeWordEngine, normalize_phrase
from .sherpa_phrase import SherpaPhraseWakeEngine

__all__ = ["SherpaPhraseWakeEngine", "WakeWordEngine", "normalize_phrase"]
