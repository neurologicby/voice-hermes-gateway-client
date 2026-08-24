"""Версионируемое SQLite-хранилище истории только на компьютере пользователя."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

MessageRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Conversation:
    session_id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Message:
    message_id: int
    session_id: str
    role: MessageRole
    text: str
    created_at: str


class HistoryStore:
    """Одна SQLite connection на owning thread; параметры всегда bind-ятся."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_history_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        try:
            self._migrate()
        except Exception:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> HistoryStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def append_message(
        self,
        session_id: str,
        role: MessageRole,
        text: str,
        *,
        created_at: datetime | None = None,
    ) -> Message:
        _validate_session_id(session_id)
        if role not in {"user", "assistant"}:
            raise ValueError("message role must be user or assistant")
        normalized = text.strip()
        if not normalized or len(normalized) > 100_000:
            raise ValueError("message text must contain 1..100000 characters")
        timestamp = _timestamp(created_at)
        title = _title(normalized)
        with self._db:
            self._db.execute(
                """
                INSERT INTO conversations(session_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (session_id, title, timestamp, timestamp),
            )
            cursor = self._db.execute(
                """
                INSERT INTO messages(session_id, role, text, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, normalized, timestamp),
            )
        message_id = cursor.lastrowid
        if message_id is None:
            raise RuntimeError("SQLite didn't return a message id")
        return Message(message_id, session_id, role, normalized, timestamp)

    def list_conversations(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> list[Conversation]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be in range 1..1000")
        parameters: list[str | int] = []
        where = ""
        if query and query.strip():
            pattern = f"%{_escape_like(query.strip())}%"
            where = """
                WHERE c.title LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR EXISTS (
                       SELECT 1 FROM messages m
                       WHERE m.session_id = c.session_id
                         AND m.text LIKE ? ESCAPE '\\' COLLATE NOCASE
                   )
            """
            parameters.extend((pattern, pattern))
        parameters.append(limit)
        rows = self._db.execute(
            f"""
            SELECT c.session_id, c.title, c.created_at, c.updated_at
            FROM conversations c
            {where}
            ORDER BY c.updated_at DESC, c.session_id
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [
            Conversation(row["session_id"], row["title"], row["created_at"], row["updated_at"])
            for row in rows
        ]

    def messages(self, session_id: str) -> list[Message]:
        _validate_session_id(session_id)
        rows = self._db.execute(
            """
            SELECT id, session_id, role, text, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
        return [
            Message(row["id"], row["session_id"], row["role"], row["text"], row["created_at"])
            for row in rows
        ]

    def export(self, session_id: str, destination: Path) -> Path:
        """Экспортирует один диалог в UTF-8 Markdown или plain text."""

        suffix = destination.suffix.lower()
        if suffix not in {".md", ".txt"}:
            raise ValueError("history export supports .md and .txt")
        messages = self.messages(session_id)
        if not messages:
            raise KeyError(f"unknown or empty session: {session_id}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".md":
            lines = [f"# VoiceGateway — {session_id}", ""]
            for message in messages:
                label = "Пользователь" if message.role == "user" else "Hermes"
                lines.extend((f"## {label}", "", message.text, ""))
        else:
            lines = []
            for message in messages:
                label = "Пользователь" if message.role == "user" else "Hermes"
                lines.append(f"[{label}] {message.text}")
        destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return destination

    def _migrate(self) -> None:
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if version > self.SCHEMA_VERSION:
            raise RuntimeError("history database was created by a newer client")
        if version == 0:
            with self._db:
                self._db.executescript(
                    """
                    CREATE TABLE conversations (
                        session_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL REFERENCES conversations(session_id)
                            ON DELETE CASCADE,
                        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                        text TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX messages_session_id_idx ON messages(session_id, id);
                    PRAGMA user_version = 1;
                    """
                )


def default_history_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "VoiceClient" / "history.db"


def _timestamp(value: datetime | None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    return current.astimezone(UTC).isoformat(timespec="milliseconds")


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
        raise ValueError("session_id must contain 1..256 characters")


def _title(text: str) -> str:
    first_line = text.splitlines()[0].strip()
    return first_line if len(first_line) <= 80 else first_line[:77].rstrip() + "..."


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


__all__ = ["Conversation", "HistoryStore", "Message", "default_history_path"]
