"""Главное окно: pairing, PTT, явный язык, transcript и локальная история."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from voice_client.history import HistoryStore
from voice_client.net.protocol import MAX_SEQUENCE, SpeechLanguage
from voice_client.wake import WakeEngineLoader, WakeResources, WakeWordEngine
from voice_client.wake.controller import WakeController


class Recorder(Protocol):
    def start(self, callback: Callable[[bytes], None]) -> None: ...

    def stop(self, timeout: float = 2.0) -> None: ...

    def set_device(self, device: int | str | None) -> None: ...


class MainWindow(QMainWindow):
    """UI никогда не выполняет WebSocket, PortAudio или SQLite migration."""

    wake_triggered = Signal()
    wake_runtime_failed = Signal(str)

    def __init__(
        self,
        *,
        worker: Any,
        recorder: Recorder,
        history: HistoryStore,
        url: str,
        device_id: str,
        user_name: str,
        on_close: Callable[[], None] | None = None,
        file_loader: Any | None = None,
        device_scanner: Any | None = None,
        on_output_device: Callable[[int | None], None] | None = None,
        microphone_device: int | None = None,
        output_device: int | None = None,
        wake_loader: WakeEngineLoader | None = None,
        wake_phrase: str = "Привет Гермес",
    ) -> None:
        super().__init__()
        self.worker = worker
        self.recorder = recorder
        self.history = history
        self._sequence = 1
        self._recording = False
        self._talking = False
        self._wake_recording = False
        self._wake_connected = False
        self._wake_loader = wake_loader
        self._wake_controller: WakeController | None = None
        self._session_id = f"local:{device_id}"
        self._closing = False
        self._on_close = on_close
        self._file_loader = file_loader
        self._device_scanner = device_scanner
        self._on_output_device = on_output_device
        self._preferred_input = microphone_device
        self._preferred_output = output_device
        self.setWindowTitle("VoiceGateway Client")
        self.setMinimumSize(780, 560)
        self.setAcceptDrops(file_loader is not None)
        self._build_ui(url, device_id, user_name)
        self._pair_retry = QTimer(self)
        self._pair_retry.setInterval(2_000)
        self._pair_retry.timeout.connect(self.worker.retry_hello)
        self.worker.event_received.connect(self.on_server_event)
        self.worker.state_changed.connect(self.on_connection_state)
        self.worker.failed.connect(self.on_failure)
        self.wake_triggered.connect(self._on_wake_triggered)
        self.wake_runtime_failed.connect(self._wake_failed)
        if self._wake_loader is not None:
            self._wake_loader.loaded.connect(self._wake_loaded)
            self._wake_loader.failed.connect(self._wake_failed)
        if self._file_loader is not None:
            self._file_loader.loaded.connect(self._send_loaded_file)
            self._file_loader.failed.connect(self._file_failed)
        if self._device_scanner is not None:
            self._device_scanner.scanned.connect(self._devices_scanned)
            self._device_scanner.failed.connect(self.on_failure)
        self._create_tray()
        self.wake_phrase_edit.setText(wake_phrase)
        self.refresh_history()
        if self._device_scanner is not None:
            self._device_scanner.refresh()

    def start(self) -> None:
        self.worker.start()

    def _build_ui(self, url: str, device_id: str, user_name: str) -> None:
        central = QWidget(self)
        root = QHBoxLayout(central)
        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        root.addWidget(splitter)

        history_panel = QWidget(splitter)
        history_layout = QVBoxLayout(history_panel)
        self.history_search = QLineEdit(history_panel)
        self.history_search.setObjectName("historySearch")
        self.history_search.setPlaceholderText("Поиск в истории")
        self.history_search.textChanged.connect(self.refresh_history)
        self.history_list = QListWidget(history_panel)
        self.history_list.setObjectName("historyList")
        self.history_list.itemSelectionChanged.connect(self._show_selected_history)
        self.export_button = QPushButton("Экспорт", history_panel)
        self.export_button.clicked.connect(self._export_selected)
        history_layout.addWidget(self.history_search)
        history_layout.addWidget(self.history_list)
        history_layout.addWidget(self.export_button)

        content = QWidget(splitter)
        content_layout = QVBoxLayout(content)
        connection_form = QFormLayout()
        self.url_edit = QLineEdit(url, content)
        self.url_edit.setObjectName("urlEdit")
        self.url_edit.setReadOnly(True)
        self.device_edit = QLineEdit(device_id, content)
        self.device_edit.setReadOnly(True)
        self.user_edit = QLineEdit(user_name, content)
        self.user_edit.setObjectName("userEdit")
        self.status_label = QLabel("Отключено", content)
        self.status_label.setObjectName("connectionStatus")
        connection_form.addRow("WebSocket:", self.url_edit)
        connection_form.addRow("Device ID:", self.device_edit)
        connection_form.addRow("Пользователь:", self.user_edit)
        connection_form.addRow("Статус:", self.status_label)
        content_layout.addLayout(connection_form)

        pairing_row = QHBoxLayout()
        self.pair_button = QPushButton("Получить код", content)
        self.pair_button.setObjectName("pairButton")
        self.pair_button.clicked.connect(self._request_pairing)
        self.pairing_label = QLabel("Pairing не выполнен", content)
        self.pairing_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pairing_row.addWidget(self.pair_button)
        pairing_row.addWidget(self.pairing_label, 1)
        content_layout.addLayout(pairing_row)

        wake_row = QHBoxLayout()
        self.wake_phrase_edit = QLineEdit(content)
        self.wake_phrase_edit.setObjectName("wakePhraseEdit")
        self.wake_phrase_edit.setPlaceholderText("Привет Гермес / Hello Hermes")
        self.wake_phrase_edit.editingFinished.connect(self._reload_wake)
        self.wake_button = QPushButton("Wake word", content)
        self.wake_button.setObjectName("wakeButton")
        self.wake_button.setCheckable(True)
        self.wake_button.setEnabled(self._wake_loader is not None)
        self.wake_button.toggled.connect(self._set_wake_enabled)
        self.wake_status = QLabel("Выключен", content)
        self.wake_status.setObjectName("wakeStatus")
        wake_row.addWidget(self.wake_phrase_edit, 1)
        wake_row.addWidget(self.wake_button)
        wake_row.addWidget(self.wake_status)
        content_layout.addLayout(wake_row)

        controls = QHBoxLayout()
        self.language_combo = QComboBox(content)
        self.language_combo.setObjectName("languageCombo")
        self.language_combo.addItem("Русский", "ru")
        self.language_combo.addItem("English", "en")
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        self.talk_button = QPushButton("Говорить", content)
        self.talk_button.setObjectName("talkButton")
        self.talk_button.pressed.connect(self._start_talking)
        self.talk_button.released.connect(self._stop_talking)
        self.mute_button = QPushButton("Mute", content)
        self.mute_button.setCheckable(True)
        self.mute_button.setObjectName("muteButton")
        self.mute_button.toggled.connect(self._set_muted)
        self.pause_button = QPushButton("Пауза", content)
        self.pause_button.setCheckable(True)
        self.pause_button.setObjectName("pauseButton")
        self.pause_button.toggled.connect(self._set_paused)
        controls.addWidget(self.language_combo)
        controls.addWidget(self.talk_button)
        controls.addWidget(self.mute_button)
        controls.addWidget(self.pause_button)
        content_layout.addLayout(controls)

        devices = QHBoxLayout()
        self.microphone_combo = QComboBox(content)
        self.microphone_combo.setObjectName("microphoneCombo")
        self.microphone_combo.setPlaceholderText("Микрофон")
        self.microphone_combo.currentIndexChanged.connect(self._microphone_changed)
        self.output_combo = QComboBox(content)
        self.output_combo.setObjectName("outputCombo")
        self.output_combo.setPlaceholderText("Динамик")
        self.output_combo.currentIndexChanged.connect(self._output_changed)
        self.refresh_devices_button = QPushButton("Обновить устройства", content)
        self.refresh_devices_button.clicked.connect(self._refresh_devices)
        devices.addWidget(self.microphone_combo)
        devices.addWidget(self.output_combo)
        devices.addWidget(self.refresh_devices_button)
        content_layout.addLayout(devices)

        self.drop_label = QLabel("Перетащите файл сюда (до 50 МБ)", content)
        self.drop_label.setObjectName("fileDropArea")
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setMinimumHeight(54)
        self.drop_label.setStyleSheet("border: 1px dashed palette(mid); padding: 12px;")
        self.drop_label.setEnabled(self._file_loader is not None)
        content_layout.addWidget(self.drop_label)

        content_layout.addWidget(QLabel("Живой текст", content))
        self.transcript = QPlainTextEdit(content)
        self.transcript.setObjectName("transcriptView")
        self.transcript.setReadOnly(True)
        content_layout.addWidget(self.transcript)
        content_layout.addWidget(QLabel("Ответ Hermes", content))
        self.answer = QPlainTextEdit(content)
        self.answer.setObjectName("answerView")
        self.answer.setReadOnly(True)
        content_layout.addWidget(self.answer)

        splitter.addWidget(history_panel)
        splitter.addWidget(content)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(central)

    def _create_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        tray = QSystemTrayIcon(icon, self)
        menu = QMenu(self)
        show_action = QAction("Показать", menu)
        show_action.triggered.connect(self.showNormal)
        mute_action = QAction("Mute", menu)
        mute_action.setCheckable(True)
        mute_action.toggled.connect(self.mute_button.setChecked)
        test_action = QAction("Тест", menu)
        test_action.triggered.connect(self.worker.send_test)
        quit_action = QAction("Выход", menu)
        quit_action.triggered.connect(self.close)
        menu.addActions((show_action, mute_action, test_action, quit_action))
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.show()
        self.tray = tray

    @Slot()
    def _request_pairing(self) -> None:
        name = self.user_edit.text().strip()
        if not name:
            self.on_failure("Введите имя пользователя")
            return
        self.worker.request_pairing(name)
        self.pairing_label.setText("Запрашиваем код…")

    @Slot()
    def _language_changed(self) -> None:
        language = cast(SpeechLanguage, self.language_combo.currentData())
        self.worker.set_language(language)
        self._reload_wake()

    @Slot()
    def _start_talking(self) -> None:
        if self._recording or self.mute_button.isChecked() or self.pause_button.isChecked():
            return
        if self._talking:
            self.worker.interrupt()
        sequence = self._sequence
        self._sequence = 1 if sequence == MAX_SEQUENCE else sequence + 1
        self.worker.begin_audio(sequence)
        try:
            self.recorder.start(self.worker.send_audio)
        except Exception as exc:
            self.worker.interrupt()
            self.on_failure(str(exc) or type(exc).__name__)
            return
        self._recording = True
        self.status_label.setText("Слушаю")

    @Slot()
    def _stop_talking(self) -> None:
        if not self._recording:
            return
        self.recorder.stop()
        self._recording = False
        self.worker.end_audio()
        self.status_label.setText("Распознавание…")

    @Slot(bool)
    def _set_muted(self, on: bool) -> None:
        if on:
            if self._recording:
                self._cancel_capture()
            elif self._wake_controller is None:
                self.worker.interrupt()
        if self._wake_controller is not None:
            self._wake_controller.set_muted(on)
            self._sync_wake_capture()
        self.worker.send_mute(on)
        self._sync_talk_button()

    @Slot(bool)
    def _set_paused(self, on: bool) -> None:
        if on:
            if self._recording:
                self._cancel_capture()
            elif self._wake_controller is None:
                self.worker.interrupt()
        if self._wake_controller is not None:
            self._wake_controller.set_paused(on)
            self._sync_wake_capture()
        self._sync_talk_button()
        self.status_label.setText("Пауза" if on else "Готов")

    @Slot(bool)
    def _set_wake_enabled(self, on: bool) -> None:
        if not on:
            self._stop_wake_capture()
            if self._wake_controller is not None:
                self._wake_controller.close()
                self._wake_controller = None
            self.wake_status.setText("Выключен")
            self._sync_talk_button()
            return
        phrase = self.wake_phrase_edit.text().strip()
        if self._wake_loader is None or not phrase:
            self.wake_button.setChecked(False)
            self.on_failure("Wake word: укажите фразу и настройте локальную модель")
            return
        self._stop_wake_capture()
        if self._wake_controller is not None:
            self._wake_controller.close()
            self._wake_controller = None
        self._cancel_capture()
        self.wake_status.setText("Загрузка…")
        self.wake_phrase_edit.setEnabled(False)
        language = cast(SpeechLanguage, self.language_combo.currentData())
        self._wake_loader.load(language, phrase)
        self._sync_talk_button()

    @Slot()
    def _reload_wake(self) -> None:
        if not self.wake_button.isChecked():
            return
        self._set_wake_enabled(True)

    @Slot(object)
    def _wake_loaded(self, engine: object) -> None:
        if isinstance(engine, WakeResources):
            resources = engine
        elif isinstance(engine, WakeWordEngine):
            resources = WakeResources(engine)
        else:
            self._wake_failed("Wake loader вернул неверный engine")
            return
        if not self.wake_button.isChecked():
            resources.close()
            return
        if self._wake_controller is not None:
            self._wake_controller.close()
        self._wake_controller = WakeController(
            resources.engine,
            self.worker,
            vad_engine=resources.vad_engine,
        )
        self._wake_controller.set_connected(self._wake_connected)
        self.wake_phrase_edit.setEnabled(True)
        self.wake_status.setText("Ожидание" if self._wake_connected else "Нет связи")
        self._sync_wake_capture()

    @Slot(str)
    def _wake_failed(self, message: str) -> None:
        self.wake_phrase_edit.setEnabled(True)
        self.wake_button.setChecked(False)
        self.on_failure(f"Wake word: {message}")

    def _wake_audio(self, pcm_s16le: bytes) -> None:
        controller = self._wake_controller
        if controller is None:
            return
        try:
            if controller.process_pcm(pcm_s16le):
                self.wake_triggered.emit()
        except Exception as exc:
            self.wake_runtime_failed.emit(str(exc) or type(exc).__name__)

    @Slot()
    def _on_wake_triggered(self) -> None:
        self.wake_status.setText("Слушаю")
        self.status_label.setText("Слушаю")

    def _sync_wake_capture(self) -> None:
        should_run = (
            self.wake_button.isChecked()
            and self._wake_controller is not None
            and self._wake_connected
            and not self.mute_button.isChecked()
            and not self.pause_button.isChecked()
        )
        if should_run and not self._wake_recording:
            try:
                self.recorder.start(self._wake_audio)
            except Exception as exc:
                self._wake_failed(str(exc) or type(exc).__name__)
                return
            self._wake_recording = True
        elif not should_run:
            self._stop_wake_capture()

    def _stop_wake_capture(self) -> None:
        if self._wake_recording:
            self.recorder.stop()
            self._wake_recording = False

    def _sync_talk_button(self) -> None:
        enabled = (
            not self.mute_button.isChecked()
            and not self.pause_button.isChecked()
            and not self.wake_button.isChecked()
        )
        self.talk_button.setEnabled(enabled)

    def _cancel_capture(self) -> None:
        if self._recording:
            self.recorder.stop()
            self._recording = False
        self.worker.interrupt()

    @Slot()
    def _refresh_devices(self) -> None:
        if self._device_scanner is not None:
            self._device_scanner.refresh()

    @Slot(object)
    def _devices_scanned(self, raw_devices: object) -> None:
        if not isinstance(raw_devices, list):
            return
        old_input = self.microphone_combo.currentData()
        old_output = self.output_combo.currentData()
        self.microphone_combo.blockSignals(True)
        self.output_combo.blockSignals(True)
        self.microphone_combo.clear()
        self.output_combo.clear()
        for device in raw_devices:
            index = getattr(device, "index", None)
            name = getattr(device, "name", "Audio device")
            if getattr(device, "input_channels", 0) > 0:
                self.microphone_combo.addItem(str(name), index)
            if getattr(device, "output_channels", 0) > 0:
                self.output_combo.addItem(str(name), index)
        _restore_combo(
            self.microphone_combo,
            old_input if old_input is not None else self._preferred_input,
        )
        _restore_combo(
            self.output_combo,
            old_output if old_output is not None else self._preferred_output,
        )
        self.microphone_combo.blockSignals(False)
        self.output_combo.blockSignals(False)
        self._microphone_changed()
        self._output_changed()

    @Slot()
    def _microphone_changed(self) -> None:
        value = self.microphone_combo.currentData()
        if isinstance(value, int):
            self._preferred_input = value
            restart_wake = self._wake_recording
            if restart_wake:
                self._stop_wake_capture()
            try:
                self.recorder.set_device(value)
            except RuntimeError as exc:
                self.on_failure(str(exc))
            if restart_wake:
                self._sync_wake_capture()

    @Slot()
    def _output_changed(self) -> None:
        value = self.output_combo.currentData()
        if isinstance(value, int):
            self._preferred_output = value
            if self._on_output_device is not None:
                self._on_output_device(value)

    @Slot(str, str, bytes)
    def _send_loaded_file(self, name: str, mime: str, payload: bytes) -> None:
        self.worker.send_file(name, mime, payload)
        self.drop_label.setText(f"Отправляется: {name}")

    @Slot(str, str)
    def _file_failed(self, path: str, error: str) -> None:
        self.on_failure(f"{Path(path).name}: {error}")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if self._file_loader is not None and any(url.isLocalFile() for url in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._file_loader is None:
            event.ignore()
            return
        accepted = False
        for url in event.mimeData().urls()[:5]:
            if url.isLocalFile() and self._file_loader.enqueue(Path(url.toLocalFile())):
                accepted = True
        if accepted:
            self.drop_label.setText("Файл добавлен в очередь")
            event.acceptProposedAction()
        else:
            event.ignore()

    @Slot(object)
    def on_server_event(self, message: object) -> None:
        if not isinstance(message, dict):
            return
        if self._wake_controller is not None:
            self._wake_controller.on_server_event(message)
        frame_type = message.get("type")
        if frame_type == "pair_code":
            code = str(message.get("code", ""))
            self.pairing_label.setText(f"Код: {code} — ждём одобрения")
            QApplication.clipboard().setText(code)
            self._pair_retry.start()
        elif frame_type == "hello_ok":
            self._pair_retry.stop()
            self._session_id = str(message.get("session", self._session_id))
            self.pairing_label.setText("Подключено")
        elif frame_type == "interim":
            self.transcript.setPlainText(str(message.get("text", "")))
        elif frame_type == "final":
            text = str(message.get("text", "")).strip()
            self.transcript.setPlainText(text)
            if text:
                self.history.append_message(self._session_id, "user", text)
                self.refresh_history()
        elif frame_type == "agent_interim":
            self.answer.setPlainText(str(message.get("text", "")))
        elif frame_type == "agent_text":
            text = str(message.get("text", "")).strip()
            self.answer.setPlainText(text)
            if text:
                self.history.append_message(self._session_id, "assistant", text)
                self.refresh_history()
        elif frame_type == "tts_start":
            self._talking = True
            self.status_label.setText("Говорю")
        elif frame_type == "tts_end":
            self._talking = False
            self.status_label.setText("Готов")
            if self.wake_button.isChecked():
                self.wake_status.setText("Ожидание")
        elif frame_type == "error":
            self.on_failure(str(message.get("message", "Ошибка сервера")))

    @Slot(str)
    def on_connection_state(self, state: str) -> None:
        self._wake_connected = state == "ready"
        if self._wake_controller is not None:
            self._wake_controller.set_connected(self._wake_connected)
            self.wake_status.setText("Ожидание" if self._wake_connected else "Нет связи")
            self._sync_wake_capture()
        labels = {
            "connecting": "Подключение…",
            "awaiting_hello": "Проверка доступа…",
            "pair_required": "Требуется pairing",
            "ready": "Готов",
            "disconnected": "Переподключение…",
            "stopped": "Отключено",
        }
        self.status_label.setText(labels.get(state, state))

    @Slot(str)
    def on_failure(self, message: str) -> None:
        self.statusBar().showMessage(message, 8_000)

    @Slot()
    def refresh_history(self) -> None:
        selected = self.history_list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        conversations = self.history.list_conversations(query=self.history_search.text())
        self.history_list.clear()
        for conversation in conversations:
            self.history_list.addItem(conversation.title)
            item = self.history_list.item(self.history_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, conversation.session_id)
            if conversation.session_id == selected_id:
                item.setSelected(True)

    @Slot()
    def _show_selected_history(self) -> None:
        item = self.history_list.currentItem()
        if item is None:
            return
        session_id = str(item.data(Qt.ItemDataRole.UserRole))
        lines = []
        for message in self.history.messages(session_id):
            label = "Вы" if message.role == "user" else "Hermes"
            lines.append(f"{label}: {message.text}")
        self.answer.setPlainText("\n\n".join(lines))

    @Slot()
    def _export_selected(self) -> None:
        item = self.history_list.currentItem()
        if item is None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт истории",
            "dialog.md",
            "Markdown (*.md);;Text (*.txt)",
        )
        if destination:
            self.history.export(
                str(item.data(Qt.ItemDataRole.UserRole)),
                Path(destination),
            )

    @Slot(QSystemTrayIcon.ActivationReason)
    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason is QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()
            self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        self._closing = True
        self._pair_retry.stop()
        if self._recording or self._wake_recording:
            self.recorder.stop()
        if self._wake_controller is not None:
            self._wake_controller.close()
        if self._wake_loader is not None:
            self._wake_loader.close()
        if not self.worker.close():
            QMessageBox.warning(self, "VoiceGateway", "Сетевой worker не остановился вовремя")
        self.history.close()
        if self._file_loader is not None:
            self._file_loader.close()
        if self._on_close is not None:
            self._on_close()
        if self.tray is not None:
            self.tray.hide()
        event.accept()


__all__ = ["MainWindow", "Recorder"]


def _restore_combo(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else (0 if combo.count() else -1))
