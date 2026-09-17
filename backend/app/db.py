"""Доступ к index.db в режиме read-only.

SQLite не любит шаринг соединений между потоками, а FastAPI обслуживает запросы
в пуле потоков, поэтому соединение открывается на каждый запрос (dependency).
"""
import re
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH


def _connect() -> sqlite3.Connection:
    # mode=ro + uri=True → открываем строго на чтение (ro, не immutable):
    # индекс может пересобираться параллельно, поэтому не обещаем неизменность файла.
    uri = f"file:{DB_PATH}?mode=ro"
    cx = sqlite3.connect(uri, uri=True, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    # Unicode-aware нижний регистр: встроенный lower() в SQLite приводит только
    # ASCII, из-за чего LIKE по кириллице чувствителен к регистру. Регистрируем
    # свою функцию ulower() для регистронезависимых фильтров.
    cx.create_function("ulower", 1, lambda s: s.lower() if isinstance(s, str) else s, deterministic=True)
    return cx


@contextmanager
def connection():
    cx = _connect()
    try:
        yield cx
    finally:
        cx.close()


def get_db():
    """FastAPI dependency: соединение на запрос."""
    cx = _connect()
    try:
        yield cx
    finally:
        cx.close()


# --- FTS5 helpers ---------------------------------------------------------

# Разбиваем пользовательский ввод на токены (буквы/цифры любых алфавитов).
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def build_match_query(q: str) -> str:
    """Готовит безопасную MATCH-строку для FTS5 из произвольного пользовательского ввода.

    Спецсимволы FTS5 (`"`, `*`, `:`, `-`, `(`, `)`, `^`) роняют парсер, поэтому
    выделяем только словарные токены и оборачиваем каждый в кавычки-фразы.
    Несколько токенов соединяются неявным AND (пробел). Последний токен получает
    префиксный `*` — поиск «по мере ввода» / по началу слова.
    Возвращает пустую строку, если значимых токенов нет.
    """
    if not q:
        return ""
    tokens = _TOKEN_RE.findall(q)
    if not tokens:
        return ""
    parts = [f'"{t}"' for t in tokens[:-1]]
    # префиксный поиск по последнему токену
    parts.append(f'"{tokens[-1]}"*')
    return " ".join(parts)


def build_or_match_query(q: str, max_tokens: int = 6) -> str:
    """MATCH-строка с OR-семантикой — фолбэк, когда неявный AND даёт 0 хитов.

    Токены соединяются явным `OR`, что расширяет выдачу (совпадение по любому
    слову). Используется чат-поиском при пустом результате точного (AND) поиска;
    обычный `/api/search` продолжает использовать :func:`build_match_query`.
    """
    if not q:
        return ""
    tokens = _TOKEN_RE.findall(q)
    if not tokens:
        return ""
    parts = [f'"{t}"' for t in tokens[:max_tokens]]
    return " OR ".join(parts)


def like_escape(s: str) -> str:
    """Экранирует спецсимволы LIKE (`%`, `_`, `\\`). Использовать с ESCAPE '\\'."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
