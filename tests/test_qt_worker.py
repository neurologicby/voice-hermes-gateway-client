from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from pytestqt.qtbot import QtBot

from voice_client.net.protocol import SpeechLanguage
from voice_client.qt_worker import AsyncioNetworkWorker


class FakeAsyncClient:
    def __init__(self) -> None:
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        self.on_state: Callable[[Any], None] | None = None
        self.stop_event: asyncio.Event | None = None
        self.calls: list[tuple[str, object, int]] = []

    def set_callbacks(
        self,
        *,
        on_event: Callable[[dict[str, Any]], None],
        on_state: Callable[[Any], None],
    ) -> None:
        self.on_event = on_event
        self.on_state = on_state

    async def run(self) -> None:
        self.stop_event = asyncio.Event()
        assert self.on_state is not None
        self.on_state("ready")
        await self.stop_event.wait()

    async def stop(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()

    def _call(self, name: str, value: object = None) -> None:
        self.calls.append((name, value, threading.get_ident()))

    def set_language(self, language: SpeechLanguage) -> None:
        self._call("language", language)

    def request_pairing(self, user_name: str) -> None:
        self._call("pair", user_name)

    def retry_hello(self) -> None:
        self._call("hello")

    def begin_audio(self, seq: int) -> None:
        self._call("begin", seq)

    def send_audio(self, pcm_s16le: bytes) -> None:
        self._call("audio", pcm_s16le)

    def end_audio(self) -> None:
        self._call("end")

    def interrupt(self) -> None:
        self._call("interrupt")

    def send_text(self, text: str) -> None:
        self._call("text", text)

    def send_mute(self, on: bool) -> None:
        self._call("mute", on)

    def send_test(self) -> None:
        self._call("test")

    def send_ping(self) -> None:
        self._call("ping")

    def send_file(self, name: str, mime: str, payload: bytes) -> None:
        self._call("file", (name, mime, payload))


def test_qt_worker_runs_client_calls_outside_ui_thread(qtbot: QtBot) -> None:
    client = FakeAsyncClient()
    worker = AsyncioNetworkWorker(client)
    ui_thread = threading.get_ident()
    with qtbot.waitSignal(worker.ready, timeout=2_000):
        worker.start()
    worker.set_language("en")
    worker.begin_audio(5)
    qtbot.waitUntil(lambda: len(client.calls) == 2, timeout=2_000)
    assert [call[:2] for call in client.calls] == [("language", "en"), ("begin", 5)]
    assert all(call[2] != ui_thread for call in client.calls)
    assert worker.close()


def test_qt_worker_cancels_client_stuck_between_reconnects(qtbot: QtBot) -> None:
    client = FakeAsyncClient()

    async def stuck_run() -> None:
        await asyncio.Event().wait()

    client.run = stuck_run  # type: ignore[method-assign]
    client.stop = _noop_stop  # type: ignore[method-assign]
    worker = AsyncioNetworkWorker(client)
    with qtbot.waitSignal(worker.ready, timeout=2_000):
        worker.start()
    assert worker.close()


async def _noop_stop() -> None:
    return None
