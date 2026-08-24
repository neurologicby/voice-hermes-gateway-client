"""Qt bridge к asyncio WebSocket loop, который никогда не работает в UI thread."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import QObject, QThread, Signal, Slot

from voice_client.net.protocol import SpeechLanguage


class AsyncVoiceClient(Protocol):
    def set_callbacks(
        self,
        *,
        on_event: Callable[[dict[str, Any]], None],
        on_state: Callable[[Any], None],
    ) -> None: ...

    async def run(self) -> None: ...

    async def stop(self) -> None: ...

    def set_language(self, language: SpeechLanguage) -> None: ...

    def request_pairing(self, user_name: str) -> None: ...

    def retry_hello(self) -> None: ...

    def begin_audio(self, seq: int) -> None: ...

    def send_audio(self, pcm_s16le: bytes) -> None: ...

    def end_audio(self) -> None: ...

    def interrupt(self) -> None: ...

    def send_text(self, text: str) -> None: ...

    def send_mute(self, on: bool) -> None: ...

    def send_test(self) -> None: ...

    def send_ping(self) -> None: ...

    def send_file(self, name: str, mime: str, payload: bytes) -> None: ...


class AsyncioNetworkWorker(QObject):
    """Владеет одним QThread и одним asyncio loop до close()."""

    event_received = Signal(object)
    state_changed = Signal(str)
    failed = Signal(str)
    ready = Signal()
    stopped = Signal()

    def __init__(self, client: AsyncVoiceClient) -> None:
        super().__init__()
        self.client = client
        self._thread = QThread()
        self._thread.setObjectName("voice-websocket-worker")
        self.moveToThread(self._thread)
        self._thread.started.connect(self._run)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client_task: asyncio.Task[None] | None = None
        self._lock = threading.Lock()
        self._stop_requested = False
        self.client.set_callbacks(
            on_event=self._emit_event,
            on_state=self._emit_state,
        )

    def start(self) -> None:
        if not self._thread.isRunning():
            self._thread.start()

    def close(self, timeout_ms: int = 3_000) -> bool:
        with self._lock:
            self._stop_requested = True
            loop = self._loop
        if loop is not None:
            asyncio.run_coroutine_threadsafe(self._shutdown_client(), loop)
        return self._thread.wait(timeout_ms)

    def set_language(self, language: SpeechLanguage) -> None:
        self._submit(self.client.set_language, language)

    def request_pairing(self, user_name: str) -> None:
        self._submit(self.client.request_pairing, user_name)

    def retry_hello(self) -> None:
        self._submit(self.client.retry_hello)

    def begin_audio(self, seq: int) -> None:
        self._submit(self.client.begin_audio, seq)

    def send_audio(self, pcm_s16le: bytes) -> None:
        self._submit(self.client.send_audio, bytes(pcm_s16le))

    def end_audio(self) -> None:
        self._submit(self.client.end_audio)

    def interrupt(self) -> None:
        self._submit(self.client.interrupt)

    def send_text(self, value: str) -> None:
        self._submit(self.client.send_text, value)

    def send_mute(self, on: bool) -> None:
        self._submit(self.client.send_mute, on)

    def send_test(self) -> None:
        self._submit(self.client.send_test)

    def send_ping(self) -> None:
        self._submit(self.client.send_ping)

    def send_file(self, name: str, mime: str, payload: bytes) -> None:
        self._submit(self.client.send_file, name, mime, bytes(payload))

    @Slot()
    def _run(self) -> None:
        try:
            asyncio.run(self._run_client())
        except Exception as exc:  # Qt boundary: исключение преобразуется в безопасный сигнал
            self.failed.emit(str(exc) or type(exc).__name__)
        finally:
            with self._lock:
                self._loop = None
            self._thread.quit()
            self.stopped.emit()

    async def _run_client(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._loop = loop
            stop_requested = self._stop_requested
        self.ready.emit()
        if stop_requested:
            await self.client.stop()
            return
        task = asyncio.create_task(self.client.run())
        with self._lock:
            self._client_task = task
        try:
            await task
        except asyncio.CancelledError:
            if not self._stop_requested:
                raise
        finally:
            with self._lock:
                self._client_task = None

    async def _shutdown_client(self) -> None:
        await self.client.stop()
        with self._lock:
            task = self._client_task
        if task is not None:
            task.cancel()

    def _submit(self, callback: Callable[..., None], *args: object) -> None:
        with self._lock:
            loop = self._loop
        if loop is None or loop.is_closed():
            self.failed.emit("Сетевой worker ещё не готов")
            return

        def invoke() -> None:
            try:
                callback(*args)
            except Exception as exc:
                self.failed.emit(str(exc) or type(exc).__name__)

        loop.call_soon_threadsafe(invoke)

    def _emit_event(self, event: dict[str, Any]) -> None:
        self.event_received.emit(event)

    def _emit_state(self, state: Any) -> None:
        value = getattr(state, "value", state)
        self.state_changed.emit(str(value))


__all__ = ["AsyncVoiceClient", "AsyncioNetworkWorker"]
