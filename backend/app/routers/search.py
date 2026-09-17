"""Глобальный / внутрикнижный поиск по FTS5 (keyword-режим).

Реальная схема FTS (см. ingest.py) отличается от SPEC §3 — используем фактические
имена колонок: pages_fts(markdown, source_id, source_title, page_index),
preparations_fts(..., prep_id), diseases_fts(name, body, disease_id).
"""
import html
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import build_match_query, get_db
from ..schemas import SearchHit

router = APIRouter(prefix="/api/search", tags=["search"])

# Сниппет строится из СЫРОГО OCR-контента, где встречаются настоящие HTML-теги
# (<br>, <img> и т.п.). Чтобы v-html на клиенте их не выполнил, оборачиваем
# совпадения не в готовые <mark>, а в plaintext-плейсхолдеры из private-use зоны
# (в тексте не встречаются), затем HTML-экранируем весь фрагмент и только потом
# заменяем плейсхолдеры на настоящие <mark>/</mark>. Итог: единственный HTML в
# сниппете — доверенные <mark>, всё остальное экранировано.
_MARK_OPEN = ""
_MARK_CLOSE = ""
# параметры snippet(): столбец, старт-тег, конец-тег, многоточие, макс. токенов
_SNIP = f"'{_MARK_OPEN}', '{_MARK_CLOSE}', '…', 20"


def _safe_snippet(snip: Optional[str]) -> Optional[str]:
    if snip is None:
        return None
    return (
        html.escape(snip)
        .replace(_MARK_OPEN, "<mark>")
        .replace(_MARK_CLOSE, "</mark>")
    )


def _search_books(db, match, source_id, limit):
    sql = (
        "SELECT source_id, source_title, page_index, "
        f"snippet(pages_fts, 0, {_SNIP}) AS snippet, rank AS score "
        "FROM pages_fts WHERE pages_fts MATCH ?"
    )
    args: list = [match]
    if source_id is not None:
        sql += " AND source_id = ?"
        args.append(source_id)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    hits = []
    for r in db.execute(sql, args).fetchall():
        hits.append(
            SearchHit(
                type="page",
                source_id=r["source_id"],
                source_title=r["source_title"],
                page_index=r["page_index"],
                snippet=_safe_snippet(r["snippet"]),
                score=r["score"],
            )
        )
    return hits


def _search_pharma(db, match, limit):
    # подсветку берём из instruction_md (col 4); если пусто — из trade_name (col 0)
    sql = (
        "SELECT prep_id, trade_name, "
        f"snippet(preparations_fts, 4, {_SNIP}) AS snip_instr, "
        f"snippet(preparations_fts, 0, {_SNIP}) AS snip_name, "
        "rank AS score FROM preparations_fts WHERE preparations_fts MATCH ? "
        "ORDER BY rank LIMIT ?"
    )
    hits = []
    for r in db.execute(sql, [match, limit]).fetchall():
        snippet = r["snip_instr"] or r["snip_name"]
        hits.append(
            SearchHit(
                type="preparation",
                ref_id=r["prep_id"],
                title=r["trade_name"],
                snippet=_safe_snippet(snippet),
                score=r["score"],
            )
        )
    return hits


def _search_diseases(db, match, limit):
    sql = (
        "SELECT disease_id, name, "
        f"snippet(diseases_fts, 1, {_SNIP}) AS snippet, rank AS score "
        "FROM diseases_fts WHERE diseases_fts MATCH ? ORDER BY rank LIMIT ?"
    )
    hits = []
    for r in db.execute(sql, [match, limit]).fetchall():
        hits.append(
            SearchHit(
                type="disease",
                ref_id=r["disease_id"],
                title=r["name"],
                snippet=_safe_snippet(r["snippet"]),
                score=r["score"],
            )
        )
    return hits


@router.get("", response_model=List[SearchHit])
def search(
    q: str = Query(..., min_length=1),
    scope: str = Query("all", pattern="^(all|books|pharma|diseases)$"),
    source_id: Optional[int] = None,
    mode: str = Query("keyword"),
    limit: int = Query(20, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
):
    if mode in ("semantic", "hybrid"):
        raise HTTPException(
            status_code=501,
            detail="mode=semantic|hybrid — фаза 2, пока не реализовано; используйте mode=keyword",
        )
    if mode != "keyword":
        raise HTTPException(status_code=400, detail="mode должен быть keyword (semantic|hybrid — фаза 2)")

    match = build_match_query(q)
    if not match:
        return []

    hits: List[SearchHit] = []
    if scope in ("all", "books"):
        hits += _search_books(db, match, source_id, limit)
    if scope in ("all", "pharma"):
        hits += _search_pharma(db, match, limit)
    if scope in ("all", "diseases"):
        hits += _search_diseases(db, match, limit)

    # rank в FTS5: меньше = релевантнее. Сортируем по возрастанию, режем общим лимитом.
    hits.sort(key=lambda h: (h.score if h.score is not None else 0.0))
    return hits[:limit]
