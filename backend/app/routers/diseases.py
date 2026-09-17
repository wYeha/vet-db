"""Эндпоинты диагностики: список болезней и карточка (структура из yml → data_json)."""
import json
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_db
from ..schemas import DiseaseDetail, DiseaseListItem

router = APIRouter(prefix="/api/diseases", tags=["diseases"])


@router.get("", response_model=List[DiseaseListItem])
def list_diseases(
    species: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db),
):
    sql = "SELECT id, slug, species, name FROM diseases WHERE 1=1"
    args: list = []
    if species:
        sql += " AND species = ?"
        args.append(species)
    sql += " ORDER BY name"
    rows = db.execute(sql, args).fetchall()
    return [
        DiseaseListItem(id=r["id"], slug=r["slug"], species=r["species"], name=r["name"])
        for r in rows
    ]


@router.get("/{disease_id}", response_model=DiseaseDetail)
def get_disease(disease_id: int, db: sqlite3.Connection = Depends(get_db)):
    r = db.execute(
        "SELECT id, slug, species, name, data_json FROM diseases WHERE id = ?",
        (disease_id,),
    ).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="disease not found")
    try:
        data = json.loads(r["data_json"]) if r["data_json"] else None
    except (json.JSONDecodeError, TypeError):
        data = None
    return DiseaseDetail(
        id=r["id"], slug=r["slug"], species=r["species"], name=r["name"], data=data
    )
