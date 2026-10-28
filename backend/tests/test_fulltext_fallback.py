"""Запасной путь онтологии: fulltext_fallback_hits на in-memory SQLite."""
import sqlite3

import pytest

from app.ontology import fulltext_fallback_hits

FILLER = " ".join(["слово"] * 30)
BIB = "\n".join(
    f"Smith, J.A. (2010) Study of tylosin number {i} in pigs and swine herds. J Vet." for i in range(6)
)


def make_db(pages):
    """pages: [(source_id, page_index, text)]"""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE VIRTUAL TABLE pages_fts USING fts5("
        "markdown, source_id UNINDEXED, source_title UNINDEXED, page_index UNINDEXED)"
    )
    for sid, p, t in pages:
        db.execute("INSERT INTO pages_fts VALUES(?,?,?,?)", (t, sid, f"Книга{sid}", p))
    return db


def test_and_hits_fields_and_safety():
    db = make_db([(1, 3, "тилозин свиньям <img src=x onerror=1><br> " + FILLER)])
    hits = fulltext_fallback_hits(db, "тилозин")
    assert len(hits) == 1
    h = hits[0]
    assert (h.type, h.origin, h.precise, h.title) == ("page", "fulltext", None, None)
    assert h.source_id == 1 and h.page_index == 3
    assert "<mark>" in h.snippet and "<img" not in h.snippet and "&lt;br" not in h.snippet


def test_or_topup_below_threshold_and_dedup():
    db = make_db([
        (1, 1, "тилозин tylosin " + FILLER),
        (1, 2, "только тилозин " + FILLER),
        (1, 3, "только tylosin " + FILLER),
    ])
    hits = fulltext_fallback_hits(db, "тилозин tylosin")
    keys = [(h.source_id, h.page_index) for h in hits]
    assert keys[0] == (1, 1)  # AND выше OR
    assert sorted(keys) == [(1, 1), (1, 2), (1, 3)]


def test_bibliography_filtered_and_only_bibliography_empty():
    db = make_db([(1, 1, BIB)])
    assert fulltext_fallback_hits(db, "tylosin") == []
    db2 = make_db([(1, 1, BIB), (1, 2, "tylosin лечение " + FILLER)])
    assert [h.page_index for h in fulltext_fallback_hits(db2, "tylosin")] == [2]


def test_limit_and_species_key():
    db = make_db([(1, i, "тилозин " + FILLER) for i in range(10)] + [(2, 0, "тилозин " + FILLER)])
    assert len(fulltext_fallback_hits(db, "тилозин", limit=8)) == 8
    hits = fulltext_fallback_hits(db, "тилозин", limit=8,
                                  species_key=lambda h: 0 if h.source_id == 2 else 1)
    assert hits[0].source_id == 2


def test_empty_keywords_no_sql():
    class Boom:
        def execute(self, *a, **k):
            raise AssertionError("SQL не должен выполняться")

    assert fulltext_fallback_hits(Boom(), "") == []
    assert fulltext_fallback_hits(Boom(), "   ") == []


def test_no_pages_fts_raises():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    with pytest.raises(sqlite3.OperationalError):
        fulltext_fallback_hits(db, "тилозин")
