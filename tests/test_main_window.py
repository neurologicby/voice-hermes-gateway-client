from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from pytestqt.qtbot import QtBot

from voice_client.audio.devices import AudioDevice
from voice_client.history import HistoryStore
from voice_client.net.protocol import SpeechLanguage
from voice_client.ui import MainWindow
from voice_client.wake import WakeWordEngine


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

    def send_file(self, name: str, mime: str, payload: bytes) -> None:
        self.calls.append(("file", (name, mime, payload)))


class FakeRecorder:
    def __init__(self) -> None:
        self.callback: Callable[[bytes], None] | None = None
        self.running = False
        self.device: int | str | None = None

    def start(self, callback: Callable[[bytes], None]) -> None:
        self.callback = callback
        self.running = True

    def stop(self, timeout: float = 2.0) -> None:
        self.running = False

    def set_device(self, device: int | str | None) -> None:
        self.device = device

    def feed(self, pcm: bytes) -> None:
        assert self.callback is not None
        self.callback(pcm)


class FakeWakeEngine(WakeWordEngine):
    def __init__(self) -> None:
        super().__init__("привет гермес")
        self.trigger = False

    def process(self, _pcm_s16le: bytes) -> bool:
        value = self.trigger
        self.trigger = False
        return value

    def reset(self) -> None:
        pass


class FakeWakeLoader(QObject):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, engine: FakeWakeEngine) -> None:
        super().__init__()
        self.engine = engine
        self.closed = False

    def load(self, _language: SpeechLanguage, _phrase: str) -> None:
        self.loaded.emit(self.engine)

    def close(self) -> None:
        self.closed = True


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


def test_audio_device_selection_updates_backends(tmp_path: Path, qtbot: QtBot) -> None:
    outputs: list[int | None] = []
    window, _, recorder = _window(tmp_path, qtbot)
    window._on_output_device = outputs.append
    window._devices_scanned(
        [
            AudioDevice(2, "Mic", 1, 0),
            AudioDevice(3, "Headset", 0, 2),
        ]
    )
    assert recorder.device == 2
    assert outputs == [3]
    assert window.microphone_combo.currentText() == "Mic"
    assert window.output_combo.currentText() == "Headset"


def test_loaded_file_is_forwarded_to_network_worker(tmp_path: Path, qtbot: QtBot) -> None:
    window, worker, _ = _window(tmp_path, qtbot)
    window._send_loaded_file("report.txt", "text/plain", b"hello")
    assert worker.calls[-1] == ("file", ("report.txt", "text/plain", b"hello"))


def test_wake_ui_keeps_background_audio_local_until_trigger(
    tmp_path: Path, qtbot: QtBot
) -> None:
    worker = FakeWorker()
    recorder = FakeRecorder()
    engine = FakeWakeEngine()
    window = MainWindow(
        worker=worker,
        recorder=recorder,
        history=HistoryStore(tmp_path / "history.db"),
        url="ws://127.0.0.1:8765/ws",
        device_id="wake-device",
        user_name="dmitry",
        wake_loader=FakeWakeLoader(engine),  # type: ignore[arg-type]
    )
    qtbot.addWidget(window)
    worker.state_changed.emit("ready")
    window.wake_button.setChecked(True)
    assert recorder.running
    recorder.feed(b"background")
    assert not any(call[0] in {"begin", "audio"} for call in worker.calls)

    engine.trigger = True
    recorder.feed(b"wake phrase")
    recorder.feed(b"command")
    assert worker.calls[-2:] == [("begin", 1), ("audio", b"command")]

    window.mute_button.setChecked(True)
    assert not recorder.running
    assert worker.calls[-1] == ("mute", True)
