from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from voice_client.file_transfer import FileTransferLoader


def test_file_loader_reads_off_ui_thread_and_detects_mime(tmp_path: Path, qtbot: QtBot) -> None:
    path = tmp_path / "report.txt"
    path.write_text("hello", encoding="utf-8")
    loader = FileTransferLoader(queue_limit=1)
    with qtbot.waitSignal(loader.loaded, timeout=2_000) as signal:
        assert loader.enqueue(path)
    assert signal.args == ["report.txt", "text/plain", b"hello"]
    loader.close()


def test_file_loader_rejects_empty_file(tmp_path: Path, qtbot: QtBot) -> None:
    path = tmp_path / "empty.txt"
    path.touch()
    loader = FileTransferLoader()
    with qtbot.waitSignal(loader.failed, timeout=2_000):
        assert not loader.enqueue(path)
    loader.close()
