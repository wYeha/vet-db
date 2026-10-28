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
