"""Юнит-тесты видового буста (species.py)."""
import sqlite3

import pytest

from app.schemas import SearchHit
from app.species import detect_query_species, rerank_by_species, source_species


@pytest.mark.parametrize("text,exp", [
    ("дозировка тилозина свиньям", {"swine"}),
    ("Свиней и поросят", {"swine"}),
    ("хряк", {"swine"}),
    ("pigs and sows", {"swine"}),
    ("для бройлеров", {"avian"}),
    ("КУРЫ несушки", {"avian"}),
    ("птица", {"avian"}),
    ("утята, гуси", {"avian"}),
    ("poultry", {"avian"}),
    ("свиньи и куры", {"swine", "avian"}),
    ("дозировка тилозина", set()),
    ("свинец в корме", set()),
    ("курс лечения", set()),
    ("курение", set()),
    ("густой раствор", set()),
    ("утро", set()),
    ("бирд", set()),
    ("", set()),
    (None, set()),
])
def test_detect(text, exp):
    assert set(detect_query_species(text)) == exp


@pytest.mark.parametrize("w", ["свиной", "свиные", "свиную", "свиного", "свиным", "свиных",
                               "свиная", "свиное", "свиному", "свиными", "свиной грипп"])
def test_swine_adjectives(w):
    assert set(detect_query_species(w)) == {"swine"}


@pytest.mark.parametrize("w", ["уткнуться", "свинцовый", "курорт", "морская свинка",
                               "морских свинок", "guinea pig"])
def test_more_negatives(w):
    assert set(detect_query_species(w)) == set()


@pytest.mark.parametrize("w", ["птичка", "куриный", "утиный", "утки", "уток", "свинка"])
def test_more_positives(w):
    assert len(detect_query_species(w)) == 1


def _sdb(rows, with_desc=True):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    if with_desc:
        db.execute("CREATE TABLE sources(id INTEGER PRIMARY KEY, title TEXT, description TEXT)")
        db.executemany("INSERT INTO sources VALUES(?,?,?)", rows)
    else:
        db.execute("CREATE TABLE sources(id INTEGER PRIMARY KEY, title TEXT)")
        db.executemany("INSERT INTO sources VALUES(?,?)", [(r[0], r[1]) for r in rows])
    return db


def test_source_species():
    db = _sdb([
        (1, "Pig farming", "Guide for pigs: sows, piglets, swine health"),
        (2, "Poultry health", "Книга про птицу, бройлеров и кур"),
        (3, "Справочник", "антимикробные препараты"),
        (4, "Mixed", "pigs and poultry, pigs and poultry"),
        (5, "Pig one", None),
    ])
    m = source_species(db)
    assert m == {1: "swine", 2: "avian", 3: None, 4: None, 5: None}


def test_source_species_title_only_column_missing():
    db = _sdb([(1, "pigs swine", None)], with_desc=False)
    assert source_species(db) == {1: "swine"}


def test_source_species_no_table():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    assert source_species(db) == {}


def _h(sid, page=1, type_="page"):
    return SearchHit(type=type_, source_id=sid, page_index=page, ref_id=None if type_ == "page" else 1)


def test_rerank_tiers_stable_nothing_lost():
    sp = {1: "avian", 2: "swine", 3: None, 4: "swine"}
    hits = [_h(1, 1), _h(2, 1), _h(3, 1), _h(4, 1), _h(2, 2), _h(None, 9)]
    out = rerank_by_species(hits, frozenset({"swine"}), sp)
    assert [(h.source_id, h.page_index) for h in out] == [
        (2, 1), (4, 1), (2, 2), (3, 1), (None, 9), (1, 1)]
    assert len(out) == len(hits)


def test_rerank_noop():
    hits = [_h(1), _h(2)]
    assert rerank_by_species(hits, frozenset(), {1: "swine"}) == hits
    assert rerank_by_species(hits, frozenset({"swine"}), {}) == hits


def test_rerank_non_page_keeps_neutral_tier():
    sp = {1: "avian", 2: "swine"}
    hits = [_h(1), _h(None, type_="disease"), _h(2)]
    out = rerank_by_species(hits, frozenset({"swine"}), sp)
    assert [h.type for h in out] == ["page", "disease", "page"]
    assert out[0].source_id == 2
