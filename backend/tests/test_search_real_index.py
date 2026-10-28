"""Интеграционные тесты на реальном data/index.db (read-only, skip если нет).

ВНИМАНИЕ: проверки привязаны к содержимому корпуса и могут упасть при пересборке индекса.
Фикстура `db` в API-тестах нужна только ради skip (без индекса TestClient-запросы бессмысленны).
`<mark>` в сниппете подставляет код (ontology._row), а не данные.
"""
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.ontology import search_ontology


def _open():
    if not os.path.exists(config.DB_PATH):
        return None
    uri = "file:" + config.DB_PATH.replace("\\", "/") + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
        db.execute("SELECT 1 FROM ontology_fts LIMIT 1")
        return db
    except sqlite3.Error:
        return None


@pytest.fixture(scope="module")
def db():
    conn = _open()
    if conn is None:
        pytest.skip("нет index.db или ontology_fts")
    yield conn
    conn.close()


def test_newcastle_wordform(db):
    hits = search_ontology(db, "Ньюкасла", 20)
    assert len(hits) >= 1
    assert any("newcastle" in h["chapter_title"].lower() or "ньюкасл" in h["snippet"].lower()
               for h in hits)


def test_tylosin_bilingual(db):
    hits = search_ontology(db, "тилозин tylosin", 30)
    blob = " ".join((h["chapter_title"] + " " + h["snippet"]).lower() for h in hits)
    assert "тилозин" in blob or "тиланик" in blob


def test_streptococcus(db):
    assert len(search_ontology(db, "стрептококкоз Streptococcus suis", 20)) >= 1


def test_snippet_mark(db):
    hits = search_ontology(db, "Newcastle", 10)
    assert hits and any("<mark>" in h["snippet"] for h in hits)


@pytest.mark.parametrize("q", ['"', "* : - ( ) ^", "тилозин\"*", "a OR b NEAR"])
def test_api_search_special_chars(db, q):
    r = TestClient(app).get("/api/search", params={"q": q})
    assert r.status_code == 200


def test_api_search_wordform(db):
    r = TestClient(app).get("/api/search", params={"q": "Ньюкасла"})
    assert r.status_code == 200
    assert len(r.json()) >= 1


# --- Этап 2 онтологии: страница-фрагмент внутри главы ----------------------

_STREP = "стрептококкоз Streptococcus suis"


@pytest.fixture(scope="module")
def pdb(db):
    try:
        db.execute("SELECT 1 FROM pages_fts LIMIT 1")
    except sqlite3.Error:
        pytest.skip("нет pages_fts")
    return db


def test_refine_strep_precise_inside_chapter(pdb):
    from app.ontology import _RANGE_MARGIN, _chapter_ranges, _end_for, refine_ontology_hits

    rows = search_ontology(pdb, _STREP, 8)
    hits = refine_ontology_hits(pdb, rows, _STREP)
    ranges = _chapter_ranges(pdb, {r["source_id"] for r in rows})
    ok = False
    for h in hits:
        if not h.precise:
            continue
        for r in rows:
            if (r["kind"] != "chapter" or r["page_index"] is None
                    or r["source_id"] != h.source_id or r["chapter_title"] != h.title):
                continue
            info = ranges[r["source_id"]]
            start = r["page_index"]
            end = _end_for(info["chapters"], start, r["chapter_title"], info["num_pages"])
            hi = float("inf") if end is None else end + _RANGE_MARGIN
            if h.page_index != start and start - _RANGE_MARGIN <= h.page_index < hi \
                    and "<mark>" in (h.snippet or ""):
                ok = True
    assert ok


def test_refine_no_dupes_and_not_more_than_rows(pdb):
    from app.ontology import refine_ontology_hits

    rows = search_ontology(pdb, _STREP, 8)
    hits = refine_ontology_hits(pdb, rows, _STREP)
    assert len(hits) <= len(rows)
    keys = [(h.source_id, h.page_index) for h in hits]
    assert len(keys) == len(set(keys))


def test_api_ontology_search_contract_unchanged(db):
    r = TestClient(app).get("/api/ontology/search", params={"q": "Newcastle"})
    assert r.status_code == 200
    items = r.json()
    assert items
    expected = {"source_id", "source_slug", "source_title", "chapter_title",
                "page_index", "kind", "snippet", "score"}
    for it in items:
        assert set(it.keys()) == expected


def test_refine_timing_soft(pdb):
    import time

    from app.ontology import refine_ontology_hits

    rows = search_ontology(pdb, _STREP, 8)
    t0 = time.perf_counter()
    refine_ontology_hits(pdb, rows, _STREP)
    assert time.perf_counter() - t0 < 2.0


def test_refine_newcastle_never_on_bibliography(pdb):
    from app.ontology import _is_bibliography, refine_ontology_hits

    q = "Ньюкасл Newcastle"
    hits = refine_ontology_hits(pdb, search_ontology(pdb, q, 8), q)
    precise = [h for h in hits if h.precise]
    assert precise
    for h in precise:
        md = pdb.execute(
            "SELECT markdown FROM pages_fts WHERE source_id = ? AND page_index = ?",
            (h.source_id, h.page_index),
        ).fetchone()["markdown"]
        assert not _is_bibliography(md), (h.source_id, h.page_index)
