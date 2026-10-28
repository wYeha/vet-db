"""Юнит-тесты этапа 2 онтологии (`refine_ontology_hits`) на in-memory SQLite.

Мини-схема повторяет реальную (pages_fts / ontology_fts с page_end и без / sources),
поэтому проверяется настоящее поведение FTS5 (BM25, MATCH, фильтры по UNINDEXED),
а не мок.
"""
import sqlite3

import pytest

from app import ontology
from app.ontology import _chapter_ranges, refine_ontology_hits

FILLER = " ".join(["слово"] * 40)


def make_db(chapters, pages, num_pages=30, with_page_end=True, source_id=1):
    """chapters: [(title, start|None, page_end|None)], pages: {page_index: text}."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE sources(id INTEGER PRIMARY KEY, title TEXT, num_pages INTEGER)")
    db.execute(
        "CREATE VIRTUAL TABLE pages_fts USING fts5("
        "markdown, source_id UNINDEXED, source_title UNINDEXED, page_index UNINDEXED)"
    )
    extra = ", page_end UNINDEXED" if with_page_end else ""
    db.execute(
        "CREATE VIRTUAL TABLE ontology_fts USING fts5(chapter_title, summary, "
        "source_id UNINDEXED, source_slug UNINDEXED, source_title UNINDEXED, "
        f"page_index UNINDEXED, kind UNINDEXED{extra}, tokenize='unicode61')"
    )
    db.execute("INSERT INTO sources VALUES(?,?,?)", (source_id, "Книга", num_pages))
    for title, start, page_end in chapters:
        if with_page_end:
            db.execute(
                "INSERT INTO ontology_fts(chapter_title,summary,source_id,source_slug,"
                "source_title,page_index,kind,page_end) VALUES(?,?,?,?,?,?,'chapter',?)",
                (title, "аннотация", source_id, "book", "Книга", start, page_end),
            )
        else:
            db.execute(
                "INSERT INTO ontology_fts(chapter_title,summary,source_id,source_slug,"
                "source_title,page_index,kind) VALUES(?,?,?,?,?,?,'chapter')",
                (title, "аннотация", source_id, "book", "Книга", start),
            )
    for p, text in pages.items():
        db.execute(
            "INSERT INTO pages_fts(markdown,source_id,source_title,page_index) VALUES(?,?,?,?)",
            (text, source_id, "Книга", p),
        )
    return db


def row(title, page, kind="chapter", sid=1, snippet="<mark>аннотация</mark>", score=-1.0):
    return {
        "source_id": sid, "source_slug": "book", "source_title": "Книга",
        "chapter_title": title if kind == "chapter" else "", "page_index": page,
        "kind": kind, "snippet": snippet, "score": score,
    }


CHAPTERS = [("Глава А", 5, 10), ("Глава Б", 10, 20)]


# 1. страница в диапазоне выигрывает у более «плотной» страницы вне диапазона
def test_page_inside_range_precise_and_safe():
    db = make_db(CHAPTERS, {
        0: "тилозин тилозин тилозин",
        7: "лечение тилозин <img src=x onerror=alert(1)> " + FILLER,
    })
    hits = refine_ontology_hits(db, [row("Глава А", 5)], "тилозин")
    assert len(hits) == 1
    h = hits[0]
    assert h.page_index == 7
    assert h.precise is True
    assert h.title == "Глава А"
    assert h.score == -1.0
    assert "<mark>" in h.snippet
    assert "&lt;img" in h.snippet and "<img" not in h.snippet


# 2. AND в диапазоне пуст → OR находит
def test_or_fallback_in_range():
    db = make_db(CHAPTERS, {
        2: "тилозин tylosin",  # вне диапазона, AND бы совпал
        8: "only tylosin here " + FILLER,
    })
    hits = refine_ontology_hits(db, [row("Глава А", 5)], "тилозин tylosin")
    assert hits[0].page_index == 8
    assert hits[0].precise is True


# 3. в диапазоне ничего → фолбэк на старт главы и аннотацию
def test_nothing_in_range_fallback():
    db = make_db(CHAPTERS, {1: "тилозин", 25: "тилозин"})
    hits = refine_ontology_hits(db, [row("Глава А", 5)], "тилозин")
    h = hits[0]
    assert h.page_index == 5
    assert h.precise is False
    assert h.snippet == "<mark>аннотация</mark>"
    assert h.title == "Глава А"


# 4. kind=book → весь источник с нижней границей от первой главы; подпись главой
def test_book_level_whole_source():
    db = make_db(CHAPTERS, {
        0: "тилозин тилозин тилозин",  # титул/оглавление — отсекается нижней границей
        12: "тилозин " + FILLER,
    })
    hits = refine_ontology_hits(db, [row(None, None, kind="book")], "тилозин")
    assert hits[0].page_index == 12
    assert hits[0].precise is True
    assert hits[0].title == "Глава Б"


def test_book_level_no_match():
    db = make_db(CHAPTERS, {12: "другое"})
    hits = refine_ontology_hits(db, [row(None, None, kind="book")], "тилозин")
    assert hits[0].page_index is None
    assert hits[0].title == "о книге"
    assert hits[0].precise is False


# 5. глава без старта → поиск по всему источнику
def test_chapter_without_start_whole_source():
    db = make_db(CHAPTERS + [("Раздел I", None, None)], {12: "тилозин " + FILLER})
    hits = refine_ontology_hits(db, [row("Раздел I", None)], "тилозин")
    assert hits[0].page_index == 12
    assert hits[0].precise is True
    assert hits[0].title == "Глава Б"  # подпись по диапазону


def test_chapter_without_start_no_known_chapters_keeps_title():
    db = make_db([("Раздел I", None, None)], {0: "тилозин"})
    hits = refine_ontology_hits(db, [row("Раздел I", None)], "тилозин")
    assert hits[0].page_index == 0
    assert hits[0].title == "Раздел I"


# 6. конец главы: page_end / next-start / num_pages; запас ±1
def test_chapter_ranges_with_and_without_page_end():
    chs = [("Глава А", 5, 15), ("Глава Б", 10, None)]
    with_end = _chapter_ranges(make_db(chs, {}, num_pages=20), {1})
    assert with_end[1]["chapters"] == [(5, 15, "Глава А"), (10, 20, "Глава Б")]
    no_col = _chapter_ranges(make_db(chs, {}, num_pages=20, with_page_end=False), {1})
    assert no_col[1]["chapters"] == [(5, 10, "Глава А"), (10, 20, "Глава Б")]
    # дубликаты стартов: next-start — строго больший; end <= start → start + 1
    dup = _chapter_ranges(make_db([("X", 5, None), ("Y", 5, None), ("Z", 8, 3)], {},
                                  num_pages=None), {1})
    assert dup[1]["chapters"] == [(5, 8, "X"), (5, 8, "Y"), (8, 9, "Z")]


def test_page_end_used_vs_next_start():
    chs = [("Глава А", 5, 15), ("Глава Б", 10, None)]
    pages = {14: "тилозин " + FILLER}
    with_end = refine_ontology_hits(make_db(chs, pages), [row("Глава А", 5)], "тилозин")
    assert with_end[0].page_index == 14 and with_end[0].precise is True
    # без колонки page_end: конец = next-start 10 (+1 запас) → стр. 14 вне диапазона
    no_col = refine_ontology_hits(make_db(chs, pages, with_page_end=False),
                                  [row("Глава А", 5)], "тилозин")
    assert no_col[0].page_index == 5 and no_col[0].precise is False


def test_last_chapter_uses_num_pages_and_margin():
    chs = [("Глава А", 5, None), ("Глава Б", 10, None)]
    db = make_db(chs, {20: "тилозин " + FILLER, 25: "тилозин"}, num_pages=20,
                 with_page_end=False)
    hits = refine_ontology_hits(db, [row("Глава Б", 10)], "тилозин")
    assert hits[0].page_index == 20  # num_pages 20 + запас 1
    # запас снизу захватывает страницу start-1
    db2 = make_db(CHAPTERS, {4: "тилозин " + FILLER})
    hits2 = refine_ontology_hits(db2, [row("Глава А", 5)], "тилозин")
    assert hits2[0].page_index == 4 and hits2[0].precise is True


# 7. дедуп
def test_dedup_parent_child_same_page():
    chs = [("Родитель", 5, 20), ("Потомок", 10, 15), ("Другая", 20, 25)]
    db = make_db(chs, {12: "тилозин " + FILLER, 22: "тилозин " + FILLER})
    rows = [row("Родитель", 5), row("Потомок", 10), row("Другая", 20)]
    hits = refine_ontology_hits(db, rows, "тилозин")
    assert [(h.page_index, h.title) for h in hits] == [(12, "Родитель"), (22, "Другая")]


def test_dedup_book_hit_gets_chapter_title():
    # стр. 4 вне [5,10) → у book-хита нет главы по диапазону (title=None);
    # глава с запасом -1 находит ту же стр. → title книги заменяется на главу
    db = make_db(CHAPTERS, {4: "тилозин " + FILLER})
    hits = refine_ontology_hits(db, [row(None, None, kind="book"), row("Глава А", 5)], "тилозин")
    assert len(hits) == 1
    assert hits[0].page_index == 4
    assert hits[0].title == "Глава А"


def test_dedup_fallback_book_replaced_by_chapter_without_start():
    db = make_db([("Раздел I", None, None)], {0: "другое"})
    hits = refine_ontology_hits(db, [row(None, None, kind="book"), row("Раздел I", None)], "тилозин")
    assert len(hits) == 1
    assert hits[0].page_index is None
    assert hits[0].title == "Раздел I"


# 8. пустые keywords → без SQL, всё фолбэк
@pytest.mark.parametrize("kw", ["", "!!! ... ---"])
def test_empty_keywords_no_sql(monkeypatch, kw):
    def boom(*a, **k):
        raise AssertionError("SQL не должен выполняться")

    monkeypatch.setattr(ontology, "_best_page", boom)
    monkeypatch.setattr(ontology, "_chapter_ranges", boom)
    hits = refine_ontology_hits(object(), [row("Глава А", 5), row(None, None, kind="book")], kw)
    assert [h.precise for h in hits] == [False, False]
    assert hits[0].page_index == 5


def test_empty_rows():
    assert refine_ontology_hits(object(), [], "тилозин") == []


# 9. бюджет FTS-вызовов и кэш
def test_fts_budget_and_cache(monkeypatch):
    chs = [(f"Г{i}", i * 3, i * 3 + 3) for i in range(8)]
    db = make_db(chs, {})  # ничего не находится → по 2 вызова (AND, OR) на строку
    calls = []
    orig = ontology._best_page

    def counting(db_, q, sid, lo, hi, stats=None):
        calls.append((q, sid, lo, hi))
        return orig(db_, q, sid, lo, hi, stats=stats)

    monkeypatch.setattr(ontology, "_best_page", counting)
    rows = [row(f"Г{i}", i * 3) for i in range(8)]
    refine_ontology_hits(db, rows, "тилозин tylosin")
    assert len(calls) <= 16
    assert len(calls) == len(set(calls))

    calls.clear()
    refine_ontology_hits(db, [row("Г1", 3)] * 8, "тилозин tylosin")
    assert len(calls) == 2  # одинаковые (q, sid, lo, hi) — из кэша


# --- Библиография: детектор и выбор страницы --------------------------------

from app.ontology import _is_bibliography  # noqa: E402

BIB_HARVARD = "\n".join([
    "Alexander, D.J. (2000) Newcastle disease and other avian paramyxoviruses. Rev Sci Tech 19, 443-462.",
    "Brown, C.C., King, D.J. and Seal, B.S. (1999) Pathogenesis of Newcastle disease in chickens. Vet Pathol 36, 125-132.",
    "Miller, P.J. and Koch, G. (2013) Newcastle disease. In: Diseases of Poultry, 13th edn. Wiley, pp. 89-107.",
    "OIE (2019) Newcastle disease (infection with Newcastle disease virus). Terrestrial Manual, Paris.",
])
BIB_VANCOUVER = "\n".join([
    "1. Smith AB, Jones CD. Tylosin pharmacokinetics in pigs. J Vet Pharm. 2015;38:12-19.",
    "2. Kowalski J, Nowak P. Mycoplasma in poultry flocks. Avian Dis. 2018;62:100-110.",
    "3. Lee K, Park S, Kim H. Tylosin resistance of Mycoplasma isolates. Vet Microbiol. 2017;201:55-61.",
])
BIB_NUMBERED = "\n".join([
    "1 Abdel-Glil, M.Y., Mor, S.K., Sharafeldin, T.A. et al. (2014). Detection of avian paramyxovirus. Avian Dis 58: 1-8.",
    "2 Aldous, E.W., Mynn, J.K., Banks, J. and Alexander, D.J. (2003). A molecular epidemiological study. Avian Pathol 32: 239-256.",
    "3 Alexander, D.J. (1988). Newcastle disease virus - an avian paramyxovirus. In: Newcastle Disease. Boston, 11-22.",
])
BIB_RU = "\n".join([
    "12. Бессарабов, Б. Ф. Болезни птиц / Б. Ф. Бессарабов. – М. : Колос, 2007. – 448 с.",
    "13. Иванов, А. В. Стрептококкоз свиней / А. В. Иванов // Ветеринария. – 2010. – № 3. – С. 12–15.",
    "14. Петров, С. И. Диагностика инфекций свиней / С. И. Петров. – СПб. : Лань, 2015. – 320 с.",
])
TEXT_WITH_CITES = "\n".join([
    "Newcastle disease is caused by virulent strains of avian paramyxovirus type 1 (Muller et al., 2012) and remains a major threat.",
    "Vaccination programs reduce losses considerably (Smith et al., 2019), but outbreaks still occur in backyard flocks worldwide.",
    "Clinical signs vary from respiratory to nervous forms depending on the strain and the immune status of the birds today.",
])


@pytest.mark.parametrize("md", [BIB_HARVARD, BIB_VANCOUVER, BIB_NUMBERED, BIB_RU])
def test_is_bibliography_true(md):
    assert _is_bibliography(md) is True


@pytest.mark.parametrize("md", [
    TEXT_WITH_CITES,
    "\n".join(BIB_HARVARD.splitlines()[:2]),  # < 3 строк-цитат
    "",
    None,
])
def test_is_bibliography_false(md):
    assert _is_bibliography(md) is False


def test_bibliography_page_skipped_for_body_page():
    # литература плотнее по термину и выигрывает BM25, но выбирается обычная страница
    db = make_db(CHAPTERS, {
        8: BIB_HARVARD + "\nNewcastle Newcastle Newcastle",
        6: "Newcastle disease introduction " + FILLER,
    })
    hits = refine_ontology_hits(db, [row("Глава А", 5)], "Newcastle")
    assert hits[0].page_index == 6
    assert hits[0].precise is True


def test_only_bibliography_in_range_fallback():
    db = make_db(CHAPTERS, {8: BIB_HARVARD, 9: BIB_NUMBERED})
    hits = refine_ontology_hits(db, [row("Глава А", 5)], "Newcastle")
    assert hits[0].precise is False
    assert hits[0].snippet == "<mark>аннотация</mark>"


def test_fallback_start_on_previous_bibliography_shifts_plus_one():
    # старт главы (5) — литература предыдущей главы, стр. 6 — обычный текст → 6
    db = make_db(CHAPTERS, {5: BIB_HARVARD, 6: "Введение в главу " + FILLER})
    hits = refine_ontology_hits(db, [row("Глава А", 5)], "тилозин")
    assert hits[0].precise is False
    assert hits[0].page_index == 6


def test_fallback_start_not_shifted_when_regular_or_next_is_bib():
    db = make_db(CHAPTERS, {5: "Обычный текст главы " + FILLER, 6: BIB_RU})
    assert refine_ontology_hits(db, [row("Глава А", 5)], "тилозин")[0].page_index == 5
    db2 = make_db(CHAPTERS, {5: BIB_HARVARD, 6: BIB_NUMBERED})
    assert refine_ontology_hits(db2, [row("Глава А", 5)], "тилозин")[0].page_index == 5
