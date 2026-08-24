from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from voice_client.history import HistoryStore


def test_history_migrates_and_keeps_messages_in_order(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    start = datetime(2026, 8, 24, 10, tzinfo=UTC)
    with HistoryStore(path) as store:
        store.append_message("voice:one", "user", "Привет, Hermes", created_at=start)
        store.append_message(
            "voice:one",
            "assistant",
            "Здравствуйте!",
            created_at=start + timedelta(seconds=1),
        )
        messages = store.messages("voice:one")
        assert [message.role for message in messages] == ["user", "assistant"]
        assert store.list_conversations()[0].title == "Привет, Hermes"

    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_search_is_literal_and_finds_message_body(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.db") as store:
        store.append_message("voice:one", "user", "100% готово")
        store.append_message("voice:two", "user", "обычный текст")
        assert [item.session_id for item in store.list_conversations(query="100%")]
        assert store.list_conversations(query="100_") == []


def test_markdown_and_text_export_are_utf8(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.db") as store:
        store.append_message("voice:one", "user", "Как дела?")
        store.append_message("voice:one", "assistant", "Хорошо.")
        markdown = store.export("voice:one", tmp_path / "dialog.md")
        plain = store.export("voice:one", tmp_path / "dialog.txt")
        assert "## Пользователь" in markdown.read_text(encoding="utf-8")
        assert "[Hermes] Хорошо." in plain.read_text(encoding="utf-8")


def test_history_rejects_invalid_input_and_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "newer.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 2")
    with pytest.raises(RuntimeError):
        HistoryStore(path)

    with HistoryStore(tmp_path / "valid.db") as store:
        with pytest.raises(ValueError):
            store.append_message("voice:one", "system", "secret")  # type: ignore[arg-type]
        with pytest.raises(KeyError):
            store.export("missing", tmp_path / "missing.md")
