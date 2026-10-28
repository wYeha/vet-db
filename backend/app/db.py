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


# Пороги грубой обрезки окончаний (стемминг «на коленке», FTS5 unicode61 без стеммера).
_MIN_STEM_TOKEN = 3     # токены короче — точное слово, без обрезки и без префикса
_CUT2_LEN = 7           # длина >= 7 → срезать 2 символа
_CUT1_LEN = 5           # длина >= 5 → срезать 1 символ (иначе не обрезаем); основа >= 4


def _is_exact(t: str) -> bool:
    """Точный токен (без префикса): чистое число или короче _MIN_STEM_TOKEN."""
    return t.isdigit() or len(t) < _MIN_STEM_TOKEN


def _stem(token: str) -> str:
    """Грубо обрезает окончание; приводит к нижнему регистру.

    Чистые числа, токены короче 3 символов и токены со смесью цифр и букв
    («b12», «h5n1») не обрезаются. Известное ограничение: беглые гласные и
    чередования не ловятся («станок» → «стано*» не найдёт «станка»).
    """
    t = token.lower()
    if _is_exact(t) or any(c.isdigit() for c in t):
        return t
    if len(t) >= _CUT2_LEN:
        return t[:-2]
    if len(t) >= _CUT1_LEN:
        return t[:-1]
    return t


def _term(token: str) -> str:
    """FTS5-терм из токена: `"основа"*` либо точное `"токен"` (числа, <3 символов)."""
    t = token.lower()
    stem = _stem(token)
    if _is_exact(t):
        return f'"{stem}"'
    return f'"{stem}"*'


def _terms(q: str) -> list:
    """Уникальные термы (порядок сохраняется) из произвольного ввода."""
    if not q:
        return []
    out: list = []
    for tok in _TOKEN_RE.findall(q):
        term = _term(tok)
        if term not in out:
            out.append(term)
    return out


def build_match_query(q: str) -> str:
    """Готовит безопасную MATCH-строку для FTS5 из произвольного пользовательского ввода.

    Спецсимволы FTS5 (`"`, `*`, `:`, `-`, `(`, `)`, `^`) роняют парсер, поэтому
    выделяем только словарные токены (`_TOKEN_RE`), каждый превращаем в
    кавычечную фразу через :func:`_term`. Токены соединяются неявным AND (пробел).
    Каждый токен получает префиксный `*` после грубой обрезки окончания
    (числа и токены <3 символов — точные). Одинаковые термы дедуплицируются.
    Возвращает пустую строку, если значимых токенов нет.
    """
    return " ".join(_terms(q))


def build_or_match_query(q: str, max_tokens: int = 6) -> str:
    """MATCH-строка с OR-семантикой — фолбэк, когда неявный AND даёт 0 хитов.

    Те же термы (префикс + обрезка), что и в :func:`build_match_query`, но через
    явный `OR`. Кап `max_tokens` применяется к уникальным термам.
    """
    return " OR ".join(_terms(q)[:max_tokens])


def like_escape(s: str) -> str:
    """Экранирует спецсимволы LIKE (`%`, `_`, `\\`). Использовать с ESCAPE '\\'."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
