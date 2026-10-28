"""Юнит-тесты нумерации страниц в аннотациях онтологии (без БД).

Модули из ontology/ грузятся по пути (это не пакет backend).
"""
import importlib.util
import os
import sqlite3

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ONT = os.path.join(ROOT, "ontology")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ONT, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


la = _load("load_annotations_t", "load_annotations.py")
nm = _load("normalize_annotation_pages_t", "normalize_annotation_pages.py")
ck = _load("check_annotation_pages_t", "check_annotation_pages.py")


def _one(head):
    _, chs = la.parse(f"## Главы\n### {head}\nтекст\n")
    assert len(chs) == 1
    return chs[0]


# --- parse --------------------------------------------------------------------

def test_parse_range():
    t, p, e, _ = _one("Введение (стр 1–3)")
    assert (t, p, e) == ("Введение", 0, 3)


def test_parse_single_page():
    t, p, e, _ = _one("Scientific Aims (стр. 5)")
    assert (t, p, e) == ("Scientific Aims", 4, None)


def test_parse_chapter_prefix():
    t, p, e, _ = _one("Хорошая подготовка (Глава 1, стр 3–18)")
    assert (t, p, e) == ("Хорошая подготовка", 2, 18)


def test_parse_dashes():
    assert _one("X (стр 2—7)")[1:3] == (1, 7)
    assert _one("X (стр 2-7)")[1:3] == (1, 7)


def test_parse_no_pages():
    t, p, e, _ = _one("Без страниц")
    assert (t, p, e) == ("Без страниц", None, None)


def test_parse_title_without_parens():
    assert _one("Глава без скобок")[0] == "Глава без скобок"


def test_parse_marker_does_not_leak_into_book():
    book, _ = la.parse(nm.MARKER + "\n## Книга\nОписание.\n\n## Главы\n### A (стр 1)\nт\n")
    assert book == "Описание."


# --- validate_chapters / маркер -------------------------------------------------

def test_validate_ok():
    chs = [("a", 0, 3, "s"), ("b", 3, 10, "s"), ("c", 9, None, "s"), ("d", None, None, "s")]
    assert la.validate_chapters(chs, 10) == []


def test_validate_errors():
    assert any("< 0" in m for m in la.validate_chapters([("a", -1, 5, "s")], 10))
    assert any(">= num_pages" in m for m in la.validate_chapters([("a", 10, None, "s")], 10))
    assert any("> num_pages" in m for m in la.validate_chapters([("a", 5, 11, "s")], 10))
    assert any("<= page_index" in m for m in la.validate_chapters([("a", 5, 5, "s")], 10))


def test_has_marker():
    assert la.has_marker(la.MARKER + "\n## Книга\n")
    assert not la.has_marker("## Книга\n" + la.MARKER)
    assert la.MARKER == nm.MARKER


# --- convert_header / migrate_text ---------------------------------------------

def test_convert_incl():
    assert nm.convert_header("### X (стр 3–4)", "incl", 5) == "### X (стр 4–5)"


def test_convert_excl():
    assert nm.convert_header("### X (стр 14–22)", "excl", 22) == "### X (стр 15–22)"


def test_convert_excl_inclusive_chapter():
    assert nm.convert_header("### X (стр 9–11)", "excl", 12) == "### X (стр 10–12)"


def test_convert_excl_last_chapter():
    assert nm.convert_header("### X (стр 9–20)", "excl", None) == "### X (стр 10–20)"


def test_convert_single_page():
    assert nm.convert_header("### Scientific Aims (стр. 70)", "excl", 71) == "### Scientific Aims (стр. 71)"


def test_convert_keeps_other_text():
    line = "### Assigning DDDA (Chapter 3) (стр. 71–117)"
    assert nm.convert_header(line, "excl", 118) == "### Assigning DDDA (Chapter 3) (стр. 72–118)"
    line = "### Оптимальный рост (Глава 2, стр 19—32)"
    assert nm.convert_header(line, "incl", 33) == "### Оптимальный рост (Глава 2, стр 20—33)"


def test_convert_no_pages_or_not_header():
    assert nm.convert_header("### Без страниц", "incl", None) == "### Без страниц"
    assert nm.convert_header("текст стр 5–6", "incl", None) == "текст стр 5–6"


SAMPLE = "## Книга\nКнига.\n\n## Главы\n### A (стр 0–4)\nт\n### B (стр 5–9)\nт\n"


def test_migrate_zero_based():
    new, ch = nm.migrate_text(SAMPLE, "0", "incl")
    assert new.startswith(nm.MARKER + "\n")
    assert "### A (стр 1–5)" in new and "### B (стр 6–10)" in new
    assert len(ch) == 2
    _, chs = la.parse(new)
    assert [(p, e) for _, p, e, _ in chs] == [(0, 5), (5, 10)]


def test_migrate_one_based_only_marker():
    new, ch = nm.migrate_text(SAMPLE, "1", None)
    assert ch == []
    assert new == nm.MARKER + "\n" + SAMPLE


def test_migrate_idempotent():
    once, _ = nm.migrate_text(SAMPLE, "0", "incl")
    twice, ch = nm.migrate_text(once, "0", "incl")
    assert twice == once and ch == []


def test_migrate_preserves_crlf():
    src = SAMPLE.replace("\n", "\r\n")
    new, _ = nm.migrate_text(src, "0", "excl")
    assert new.startswith(nm.MARKER + "\r\n")
    assert "\n" not in new.replace("\r\n", "")


# --- check-скрипт ----------------------------------------------------------------

def test_title_tokens_strip_numbering():
    assert ck.title_tokens("Глава 1. Обзор литературы") == ["обзор", "литературы"]
    assert ck.title_tokens("3.41. Стрептококкоз поросят") == ["стрептококкоз", "поросят"]
    assert ck.title_tokens("А. Введение") == ["введение"]
    assert ck.title_tokens("Chapter 4. Tetracyclines") == ["tetracyclines"]


def test_title_found():
    md = "колонтитул\n\n# Обзор литературы\n\nтекст"
    assert ck.title_found(md, "Глава 1. Обзор литературы")
    assert ck.title_found(md, "Глава 1. Обзор литературы", headings_only=True)
    md2 = "ГЛАВА 1 Обзор литературы\n\n## Другое"
    assert ck.title_found(md2, "Глава 1. Обзор литературы")
    assert not ck.title_found(md2, "Глава 1. Обзор литературы", headings_only=True)
    assert not ck.title_found("", "Обзор")
    assert not ck.title_found("текст", "")


def test_book_verdict():
    assert ck.book_verdict({-2: 0, -1: 10, 0: 1, 1: 0})[0] == "1-based"
    assert ck.book_verdict({-2: 0, -1: 1, 0: 10, 1: 2})[0] == "0-based"
    assert ck.book_verdict({-2: 0, -1: 3, 0: 3, 1: 0})[0] == "неясно"
    assert ck.book_verdict({-2: 0, -1: 0, 0: 0, 1: 0})[0] == "неясно"


def test_loader_is_argmax():
    assert ck.loader_is_argmax({-2: 0, -1: 5, 0: 5, 1: 1})
    assert not ck.loader_is_argmax({-2: 0, -1: 1, 0: 5, 1: 1})


def _res(counts, n, marker=True):
    return dict(n=n, counts=counts, verdict=ck.book_verdict(counts)[0], marker=marker)


def test_strict_status():
    ok = {-2: 0, -1: 7, 0: 6, 1: 5}
    assert ck.strict_status(_res(ok, 9))[0] == "ok"
    assert ck.strict_status(_res(ok, 9, marker=False))[0] == "error"
    assert ck.strict_status(_res({-2: 0, -1: 1, 0: 5, 1: 0}, 5))[0] == "error"   # 0-based
    assert ck.strict_status(_res({-2: 0, -1: 1, 0: 5, 1: 0}, 2))[0] == "error"   # 0-based при n<3
    tie = {-2: 0, -1: 3, 0: 3, 1: 0}
    zeros = {-2: 0, -1: 0, 0: 0, 1: 0}
    assert ck.strict_status(_res(tie, 3))[0] == "error"      # ничья при n ≥ 3
    assert ck.strict_status(_res(zeros, 4))[0] == "error"    # все нули при n ≥ 3
    assert ck.strict_status(_res(zeros, 2))[0] == "warn"     # неясно при n < 3


# --- загрузчик: --strict не трогает БД --------------------------------------------

def _mini_db(path):
    cx = sqlite3.connect(path)
    cx.executescript("""
        CREATE TABLE sources(id INTEGER PRIMARY KEY, slug TEXT, title TEXT, num_pages INTEGER,
                             description TEXT);
        CREATE TABLE toc(id INTEGER PRIMARY KEY, source_id INTEGER, title TEXT, level INTEGER,
                         page_index INTEGER, origin TEXT);
        CREATE TABLE ontology_fts(sentinel TEXT);
        INSERT INTO ontology_fts VALUES('old');
        INSERT INTO sources VALUES(1, 'book', 'Книга', 10, 'старое описание');
    """)
    cx.commit()
    cx.close()


def _write_ann(d, text):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "book.md"), "w", encoding="utf-8", newline="") as f:
        f.write(text)


ANN_OK = la.MARKER + "\n## Книга\nНовое описание.\n\n## Главы\n### Глава (стр 1–3)\nтекст\n"


@pytest.mark.parametrize("bad", [
    ANN_OK.split("\n", 1)[1],                      # нет маркера
    ANN_OK.replace("стр 1–3", "стр 1–11"),          # page_end > num_pages
])
def test_loader_strict_does_not_touch_db(tmp_path, bad, capsys):
    db = str(tmp_path / "index.db")
    ann = str(tmp_path / "ann")
    _mini_db(db)
    _write_ann(ann, bad)
    with open(db, "rb") as f:
        before = f.read()
    with pytest.raises(SystemExit) as e:
        la.main(["--strict", "--db", db, "--ann", ann])
    assert e.value.code == 1
    with open(db, "rb") as f:
        assert f.read() == before
    cx = sqlite3.connect(db)
    assert cx.execute("SELECT sentinel FROM ontology_fts").fetchall() == [("old",)]
    assert cx.execute("SELECT description FROM sources").fetchone()[0] == "старое описание"
    cx.close()


def test_loader_strict_ok_loads(tmp_path, capsys):
    db = str(tmp_path / "index.db")
    ann = str(tmp_path / "ann")
    _mini_db(db)
    _write_ann(ann, ANN_OK)
    la.main(["--strict", "--db", db, "--ann", ann])
    cx = sqlite3.connect(db)
    rows = cx.execute("SELECT kind, page_index, page_end FROM ontology_fts ORDER BY kind").fetchall()
    assert rows == [("book", None, None), ("chapter", 0, 3)]
    assert cx.execute("SELECT description FROM sources").fetchone()[0] == "Новое описание."
    cx.close()


def test_loader_non_strict_loads_with_warnings(tmp_path, capsys):
    db = str(tmp_path / "index.db")
    ann = str(tmp_path / "ann")
    _mini_db(db)
    _write_ann(ann, ANN_OK.split("\n", 1)[1])
    la.main(["--db", db, "--ann", ann])
    assert "нет маркера" in capsys.readouterr().out
    cx = sqlite3.connect(db)
    assert cx.execute("SELECT COUNT(*) FROM ontology_fts").fetchone()[0] == 2
    cx.close()
