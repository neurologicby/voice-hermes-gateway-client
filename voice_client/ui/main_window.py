"""Главное окно: pairing, PTT, явный язык, transcript и локальная история."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QAction, QCloseEvent
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


class Recorder(Protocol):
    def start(self, callback: Callable[[bytes], None]) -> None: ...

    def stop(self, timeout: float = 2.0) -> None: ...


class MainWindow(QMainWindow):
    """UI никогда не выполняет WebSocket, PortAudio или SQLite migration."""

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
    ) -> None:
        super().__init__()
        self.worker = worker
        self.recorder = recorder
        self.history = history
        self._sequence = 1
        self._recording = False
        self._talking = False
        self._session_id = f"local:{device_id}"
        self._closing = False
        self._on_close = on_close
        self.setWindowTitle("VoiceGateway Client")
        self.setMinimumSize(780, 560)
        self._build_ui(url, device_id, user_name)
        self._pair_retry = QTimer(self)
        self._pair_retry.setInterval(2_000)
        self._pair_retry.timeout.connect(self.worker.retry_hello)
        self.worker.event_received.connect(self.on_server_event)
        self.worker.state_changed.connect(self.on_connection_state)
        self.worker.failed.connect(self.on_failure)
        self._create_tray()
        self.refresh_history()

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
            self._cancel_capture()
        self.worker.send_mute(on)
        self.talk_button.setEnabled(not on and not self.pause_button.isChecked())

    @Slot(bool)
    def _set_paused(self, on: bool) -> None:
        if on:
            self._cancel_capture()
        self.talk_button.setEnabled(not on and not self.mute_button.isChecked())
        self.status_label.setText("Пауза" if on else "Готов")

    def _cancel_capture(self) -> None:
        if self._recording:
            self.recorder.stop()
            self._recording = False
        self.worker.interrupt()

    @Slot(object)
    def on_server_event(self, message: object) -> None:
        if not isinstance(message, dict):
            return
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
        elif frame_type == "error":
            self.on_failure(str(message.get("message", "Ошибка сервера")))

    @Slot(str)
    def on_connection_state(self, state: str) -> None:
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
        if self._recording:
            self.recorder.stop()
        if not self.worker.close():
            QMessageBox.warning(self, "VoiceGateway", "Сетевой worker не остановился вовремя")
        self.history.close()
        if self._on_close is not None:
            self._on_close()
        if self.tray is not None:
            self.tray.hide()
        event.accept()


__all__ = ["MainWindow", "Recorder"]
