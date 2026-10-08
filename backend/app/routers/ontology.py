"""Поиск по смысловым аннотациям (онтологии): FTS по ontology_fts.

Аннотации — сгенерированный Claude/субагентами «смысловой» слой над книгами
(описание книги + аннотации глав). Эндпоинт — тонкая обёртка над ядром
`app.ontology.search_ontology` (единый источник SQL, его же зовёт чат).
JSON-контракт эндпоинта неизменен.
"""
import sqlite3
from typing import List

from fastapi import APIRouter, Depends, Query

from ..db import get_db
from ..ontology import search_ontology

router = APIRouter(prefix="/api/ontology", tags=["ontology"])


@router.get("/search")
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: sqlite3.Connection = Depends(get_db),
) -> List[dict]:
    # Мягкая деградация (симметрично чату): старый index.db без ontology_fts →
    # пустой список вместо 500.
    try:
        return search_ontology(db, q, limit)
    except sqlite3.OperationalError:
        return []
