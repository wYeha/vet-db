"""Ядро поиска по смысловым аннотациям (онтологии): FTS по ontology_fts.

Аннотации — сгенерированный Claude/субагентами «смысловой» слой над книгами
(описание книги + аннотации глав). Поиск как обычный FTS: сначала строгий AND,
при малом числе хитов — добор OR (как в чате). Сниппет безопасен (html-
экранирование + только доверенный <mark>).

Модуль зависит только от `db.py` (без роутеров), поэтому его переиспользуют оба
потребителя: REST-роутер `/api/ontology/search` и inline-чат — без дублирования
SQL и без импорта роутера из роутера.
"""
import html
from typing import List

from .db import build_match_query, build_or_match_query

_SQL = (
    "SELECT source_id, source_slug, source_title, chapter_title, page_index, kind, "
    "snippet(ontology_fts, 1, char(2), char(3), '…', 20) AS snip, rank "
    "FROM ontology_fts WHERE ontology_fts MATCH ? ORDER BY rank LIMIT ?"
)

# Порог «строгих» хитов, ниже которого домешиваем OR-выдачу.
_MIN_STRONG_HITS = 3


def _row(r) -> dict:
    snip = html.escape(r["snip"] or "").replace(chr(2), "<mark>").replace(chr(3), "</mark>")
    return {
        "source_id": r["source_id"],
        "source_slug": r["source_slug"],
        "source_title": r["source_title"],
        "chapter_title": r["chapter_title"],
        "page_index": r["page_index"],
        "kind": r["kind"],
        "snippet": snip,
        "score": r["rank"],
    }


def _run(db, match: str, limit: int) -> List[dict]:
    if not match:
        return []
    return [_row(r) for r in db.execute(_SQL, (match, limit)).fetchall()]


def search_ontology(db, q: str, limit: int = 20) -> List[dict]:
    """Поиск по онтологии по готовому соединению `db`.

    Сначала строгий AND; при нехватке строгих хитов (< _MIN_STRONG_HITS) —
    добор OR-выдачи с дедупом по `(source_id, chapter_title, kind)`.
    Возвращает список dict-хитов (тот же контракт, что у `/api/ontology/search`).
    """
    hits = _run(db, build_match_query(q), limit)
    if len(hits) < _MIN_STRONG_HITS:
        seen = {(h["source_id"], h["chapter_title"], h["kind"]) for h in hits}
        for h in _run(db, build_or_match_query(q), limit):
            key = (h["source_id"], h["chapter_title"], h["kind"])
            if key not in seen:
                hits.append(h)
                seen.add(key)
            if len(hits) >= limit:
                break
    return hits[:limit]
