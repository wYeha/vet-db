"""Хранилище истории чатов в отдельной writable-БД `data/history.db`.

Важно: это НЕ `index.db`. `index.db` открывается строго read-only (см. db.py) и
пересобирается из S3 скриптом ingest — писать туда нельзя. История же должна
переживать перезапуск, поэтому живёт в отдельном SQLite-файле.

Модель конкурентности: соединение-на-операцию (открыть → выполнить → закрыть),
как и для index.db. Это безопасно для пула потоков FastAPI. WAL допускает
конкурентных читателей и одного писателя; `busy_timeout` смягчает гонки записи.

История — единая общая база на всех пользователей (авторизации в проекте нет).
Осознанное ограничение внутреннего инструмента (см. README).
"""
from __future__ import annotations

import datetime
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from .config import HISTORY_DB_PATH


def _now() -> str:
    """ISO-8601 UTC-таймстамп."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    # Открываем в обычном (writable) режиме; директорию гарантируем в init_schema.
    cx = sqlite3.connect(HISTORY_DB_PATH, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    # WAL + мягкие настройки долговечности/устойчивости к параллельным записям.
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=NORMAL")
    cx.execute("PRAGMA busy_timeout=5000")
    cx.execute("PRAGMA foreign_keys=ON")
    return cx


@contextmanager
def connection():
    cx = _connect()
    try:
        yield cx
    finally:
        cx.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content TEXT NOT NULL,
    hits_json TEXT,
    usage_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""


def init_schema() -> None:
    """Идемпотентная инициализация схемы; создаёт директорию data/ при отсутствии."""
    os.makedirs(os.path.dirname(os.path.abspath(HISTORY_DB_PATH)), exist_ok=True)
    with connection() as cx:
        cx.executescript(_SCHEMA)
        cx.commit()


# --- CRUD (каждая операция — короткая транзакция) --------------------------

def create_conversation(title: Optional[str]) -> int:
    now = _now()
    with connection() as cx:
        cur = cx.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        cx.commit()
        return int(cur.lastrowid)


def conversation_exists(conversation_id: int) -> bool:
    with connection() as cx:
        row = cx.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return row is not None


def add_message(
    conversation_id: int,
    role: str,
    content: str,
    hits_json: Optional[str] = None,
    usage_json: Optional[str] = None,
) -> int:
    now = _now()
    with connection() as cx:
        cur = cx.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content, hits_json, usage_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, hits_json, usage_json, now),
        )
        cx.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        cx.commit()
        return int(cur.lastrowid)


def list_conversations(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    with connection() as cx:
        rows = cx.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)
                       AS message_count
            FROM conversations c
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation(conversation_id: int) -> Optional[Dict[str, Any]]:
    with connection() as cx:
        row = cx.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row is not None else None


def get_conversation_messages(conversation_id: int) -> List[Dict[str, Any]]:
    with connection() as cx:
        rows = cx.execute(
            "SELECT id, role, content, hits_json, usage_json, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_all() -> int:
    """Полностью очищает историю: все сообщения и беседы. Возвращает число
    удалённых бесед. Разрушающая операция — вызывается по ЯВНОМУ запросу
    пользователя из UI (кнопка + подтверждение). История общая → чистит для всех.
    """
    with connection() as cx:
        n = cx.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        cx.execute("DELETE FROM messages")
        cx.execute("DELETE FROM conversations")
        cx.commit()
        return int(n)
