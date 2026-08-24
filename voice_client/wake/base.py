"""Независимый контракт локального wake-word движка."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalize_phrase(value: str) -> str:
    """Нормализовать распознанный текст без языкового автоопределения."""

    return " ".join(_NON_WORD.sub(" ", value.casefold()).split())


class WakeWordEngine(ABC):
    """Получает только PCM S16LE mono 16 kHz и сообщает о локальном trigger."""

    sample_rate = 16_000

    def __init__(self, phrase: str) -> None:
        self.phrase = normalize_phrase(phrase)
        if not self.phrase:
            raise ValueError("wake phrase must not be empty")

    @abstractmethod
    def process(self, pcm_s16le: bytes) -> bool:
        """Вернуть True ровно один раз при обнаружении фразы."""

    @abstractmethod
    def reset(self) -> None:
        """Удалить накопленный звук и decoder state."""

    def close(self) -> None:
        self.reset()


__all__ = ["WakeWordEngine", "normalize_phrase"]
