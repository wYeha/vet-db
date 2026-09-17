"""Эндпоинты источников: список, карточка, оглавление, страницы, PDF."""
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from ..db import get_db, like_escape
from .. import s3
from ..schemas import PageDetail, SourceDetail, SourceListItem, TocItem

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=List[SourceListItem])
def list_sources(
    q: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db),
):
    sql = (
        "SELECT id, slug, title, num_pages, "
        "pdf_s3key IS NOT NULL AS has_pdf, source_url FROM sources WHERE 1=1"
    )
    args: list = []
    if q:
        sql += " AND ulower(title) LIKE ulower(?) ESCAPE '\\'"
        args.append(f"%{like_escape(q)}%")
    sql += " ORDER BY title"
    rows = db.execute(sql, args).fetchall()
    return [
        SourceListItem(
            id=r["id"],
            slug=r["slug"],
            title=r["title"],
            num_pages=r["num_pages"],
            has_pdf=bool(r["has_pdf"]),
            source_url=r["source_url"],
        )
        for r in rows
    ]


@router.get("/{source_id}", response_model=SourceDetail)
def get_source(source_id: int, db: sqlite3.Connection = Depends(get_db)):
    r = db.execute(
        "SELECT id, title, num_pages, pdf_s3key, source_url, description "
        "FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="source not found")
    has_pdf = r["pdf_s3key"] is not None
    return SourceDetail(
        id=r["id"],
        title=r["title"],
        num_pages=r["num_pages"],
        has_pdf=has_pdf,
        # ссылка на собственный редирект-эндпоинт (сам presign делается там)
        pdf_url=f"/api/sources/{source_id}/pdf" if has_pdf else None,
        source_url=r["source_url"],
        description=r["description"],
    )


@router.get("/{source_id}/toc", response_model=List[TocItem])
def get_toc(
    source_id: int,
    origin: str = "all",
    db: sqlite3.Connection = Depends(get_db),
):
    exists = db.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail="source not found")
    if origin not in ("all", "curated", "markdown"):
        raise HTTPException(status_code=400, detail="origin must be all|curated|markdown")
    sql = "SELECT title, level, page_index, origin FROM toc WHERE source_id = ?"
    args: list = [source_id]
    if origin != "all":
        sql += " AND origin = ?"
        args.append(origin)
    # NULL page_index (курированные главы без страниц) — в конец, устойчиво к NULL
    sql += " ORDER BY page_index IS NULL, page_index, id"
    rows = db.execute(sql, args).fetchall()
    return [
        TocItem(
            title=r["title"],
            level=r["level"],
            page_index=r["page_index"],
            origin=(r["origin"] if "origin" in r.keys() else "markdown"),
        )
        for r in rows
    ]


@router.get("/{source_id}/pages/{index}", response_model=PageDetail)
def get_page(source_id: int, index: int, db: sqlite3.Connection = Depends(get_db)):
    src = db.execute("SELECT num_pages FROM sources WHERE id = ?", (source_id,)).fetchone()
    if src is None:
        raise HTTPException(status_code=404, detail="source not found")
    row = db.execute(
        "SELECT markdown FROM pages WHERE source_id = ? AND page_index = ?",
        (source_id, index),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="page index out of range")
    return PageDetail(
        source_id=source_id,
        page_index=index,
        markdown=row["markdown"] or "",
        num_pages=src["num_pages"],
    )


@router.get("/{source_id}/pdf")
def get_pdf(source_id: int, db: sqlite3.Connection = Depends(get_db)):
    r = db.execute("SELECT pdf_s3key FROM sources WHERE id = ?", (source_id,)).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="source not found")
    if not r["pdf_s3key"]:
        raise HTTPException(status_code=404, detail="no pdf for this source")
    try:
        url = s3.presign_pdf(r["pdf_s3key"])
    except s3.S3NotConfigured:
        raise HTTPException(status_code=503, detail="S3 is not configured (AK/SK missing)")
    return RedirectResponse(url, status_code=302)
