"""Bounded off-UI file loading for protocol v1 uploads."""

from __future__ import annotations

import mimetypes
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from voice_client.net.ws_client import MAX_FILE_BYTES


class FileTransferLoader(QObject):
    """Читает только выбранные пользователем файлы, по одному, вне Qt thread."""

    loaded = Signal(str, str, bytes)
    failed = Signal(str, str)
    _STOP = object()

    def __init__(self, *, queue_limit: int = 2) -> None:
        super().__init__()
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        self._queue: queue.Queue[Path | object] = queue.Queue(queue_limit)
        self._thread = threading.Thread(
            target=self._consume,
            name="voice-file-loader",
            daemon=True,
        )
        self._closed = False
        self._thread.start()

    def enqueue(self, path: Path) -> bool:
        if self._closed:
            return False
        candidate = path.resolve()
        try:
            size = candidate.stat().st_size
        except OSError as exc:
            self.failed.emit(str(path), str(exc))
            return False
        if not candidate.is_file() or not 1 <= size <= MAX_FILE_BYTES:
            self.failed.emit(str(path), "Файл должен иметь размер от 1 байта до 50 МБ")
            return False
        try:
            self._queue.put_nowait(candidate)
        except queue.Full:
            self.failed.emit(str(path), "Очередь файлов заполнена")
            return False
        return True

    def close(self, timeout: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put_nowait(self._STOP)
        self._thread.join(timeout)

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._STOP:
                return
            if not isinstance(item, Path):
                continue
            try:
                payload = item.read_bytes()
            except OSError as exc:
                self.failed.emit(str(item), str(exc))
                continue
            if self._closed:
                continue
            mime = mimetypes.guess_type(item.name)[0] or "application/octet-stream"
            self.loaded.emit(item.name, mime, payload)


__all__ = ["FileTransferLoader"]
