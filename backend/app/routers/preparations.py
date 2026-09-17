"""Эндпоинты фармы: список с фильтрами и карточка препарата.

Фильтры drug_class / target_animals заполнены только у origin='drugs' (79 шт);
у galen они пустые — фильтр по ним ожидаемо отсекает galen.
"""
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..db import build_match_query, get_db, like_escape
from ..schemas import PreparationDetail, PreparationListItem

router = APIRouter(prefix="/api/preparations", tags=["preparations"])


@router.get("", response_model=List[PreparationListItem])
def list_preparations(
    drug_class: Optional[str] = None,
    animal: Optional[str] = None,
    q: Optional[str] = None,
    origin: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    args: list = []
    # Поиск по названию/инструкции — через FTS. Вместо материализации всех
    # совпавших id в IN(...) (тысячи плейсхолдеров → риск на старых SQLite и
    # потеря ранжирования) джойним FTS как подзапрос и сортируем по его rank.
    if q:
        match = build_match_query(q)
        if not match:
            return []
        sql = (
            "SELECT p.id, p.origin, p.trade_name, p.generic_name, p.drug_class, "
            "p.target_animals, p.manufacturer "
            "FROM preparations p "
            "JOIN (SELECT prep_id, rank FROM preparations_fts WHERE preparations_fts MATCH ?) f "
            "ON f.prep_id = p.id WHERE 1=1"
        )
        args.append(match)
        order_by = " ORDER BY f.rank"
    else:
        sql = (
            "SELECT p.id, p.origin, p.trade_name, p.generic_name, p.drug_class, "
            "p.target_animals, p.manufacturer FROM preparations p WHERE 1=1"
        )
        order_by = " ORDER BY p.trade_name"

    if origin:
        sql += " AND p.origin = ?"
        args.append(origin)
    if drug_class:
        sql += " AND ulower(p.drug_class) LIKE ulower(?) ESCAPE '\\'"
        args.append(f"%{like_escape(drug_class)}%")
    if animal:
        sql += " AND ulower(p.target_animals) LIKE ulower(?) ESCAPE '\\'"
        args.append(f"%{like_escape(animal)}%")
    sql += order_by + " LIMIT ? OFFSET ?"
    args += [limit, offset]

    rows = db.execute(sql, args).fetchall()
    return [
        PreparationListItem(
            id=r["id"],
            origin=r["origin"],
            trade_name=r["trade_name"],
            generic_name=r["generic_name"],
            drug_class=r["drug_class"],
            target_animals=r["target_animals"],
            manufacturer=r["manufacturer"],
        )
        for r in rows
    ]


@router.get("/{prep_id}", response_model=PreparationDetail)
def get_preparation(prep_id: int, db: sqlite3.Connection = Depends(get_db)):
    r = db.execute(
        "SELECT id, origin, trade_name, generic_name, drug_class, dosage_form, route, "
        "target_animals, manufacturer, reg_number, instruction_md "
        "FROM preparations WHERE id = ?",
        (prep_id,),
    ).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="preparation not found")
    return PreparationDetail(**{k: r[k] for k in r.keys()})
