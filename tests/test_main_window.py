from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from pytestqt.qtbot import QtBot

from voice_client.history import HistoryStore
from voice_client.net.protocol import SpeechLanguage
from voice_client.ui import MainWindow


class FakeWorker(QObject):
    event_received = Signal(object)
    state_changed = Signal(str)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, object]] = []

    def start(self) -> None:
        self.calls.append(("start", None))

    def close(self) -> bool:
        self.calls.append(("close", None))
        return True

    def set_language(self, language: SpeechLanguage) -> None:
        self.calls.append(("language", language))

    def request_pairing(self, user_name: str) -> None:
        self.calls.append(("pair", user_name))

    def retry_hello(self) -> None:
        self.calls.append(("hello", None))

    def begin_audio(self, seq: int) -> None:
        self.calls.append(("begin", seq))

    def send_audio(self, pcm_s16le: bytes) -> None:
        self.calls.append(("audio", pcm_s16le))

    def end_audio(self) -> None:
        self.calls.append(("end", None))

    def interrupt(self) -> None:
        self.calls.append(("interrupt", None))

    def send_mute(self, on: bool) -> None:
        self.calls.append(("mute", on))

    def send_test(self) -> None:
        self.calls.append(("test", None))


class FakeRecorder:
    def __init__(self) -> None:
        self.callback: Callable[[bytes], None] | None = None
        self.running = False

    def start(self, callback: Callable[[bytes], None]) -> None:
        self.callback = callback
        self.running = True

    def stop(self, timeout: float = 2.0) -> None:
        self.running = False


def _window(tmp_path: Path, qtbot: QtBot) -> tuple[MainWindow, FakeWorker, FakeRecorder]:
    worker = FakeWorker()
    recorder = FakeRecorder()
    window = MainWindow(
        worker=worker,
        recorder=recorder,
        history=HistoryStore(tmp_path / "history.db"),
        url="ws://127.0.0.1:8765/ws",
        device_id="9f5e5b18-0d07-47da-8ed4-4c3a67dd535e",
        user_name="dmitry",
    )
    qtbot.addWidget(window)
    return window, worker, recorder


def test_ptt_language_mute_and_state_are_wired(tmp_path: Path, qtbot: QtBot) -> None:
    window, worker, recorder = _window(tmp_path, qtbot)
    window.language_combo.setCurrentIndex(1)
    assert worker.calls[-1] == ("language", "en")

    window.talk_button.pressed.emit()
    assert recorder.running
    assert worker.calls[-1] == ("begin", 1)
    window.talk_button.released.emit()
    assert not recorder.running
    assert worker.calls[-1] == ("end", None)

    window.mute_button.setChecked(True)
    assert not window.talk_button.isEnabled()
    assert worker.calls[-1] == ("mute", True)
    worker.state_changed.emit("ready")
    assert window.status_label.text() == "Готов"


def test_protocol_events_update_views_and_local_history(tmp_path: Path, qtbot: QtBot) -> None:
    window, worker, _ = _window(tmp_path, qtbot)
    worker.event_received.emit({"type": "hello_ok", "session": "voice:dmitry", "proto": 1})
    worker.event_received.emit({"type": "final", "seq": 1, "text": "Привет"})
    worker.event_received.emit({"type": "agent_text", "text": "Здравствуйте"})
    assert window.transcript.toPlainText() == "Привет"
    assert window.answer.toPlainText() == "Здравствуйте"
    assert window.history_list.count() == 1
    assert [message.text for message in window.history.messages("voice:dmitry")] == [
        "Привет",
        "Здравствуйте",
    ]


def test_barge_in_is_submitted_before_new_turn(tmp_path: Path, qtbot: QtBot) -> None:
    window, worker, _ = _window(tmp_path, qtbot)
    worker.event_received.emit(
        {
            "type": "tts_start",
            "stream_id": "one",
            "format": {"sample_rate": 24_000, "channels": 1, "sample_width": 2},
        }
    )
    window.talk_button.pressed.emit()
    assert worker.calls[-2:] == [("interrupt", None), ("begin", 1)]
