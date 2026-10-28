"""Регрессия нумерации страниц аннотаций на реальном data/index.db (read-only, skip если нет).

Данные берутся из ФАЙЛОВ аннотаций через `parse` (не из ontology_fts), чтобы не зависеть
от того, перезапущен ли ontology/load_annotations.py. ontology_fts сверяется только
если индекс свежий.
ВНИМАНИЕ: выборка глав привязана к содержимому корпуса.
"""
import glob
import importlib.util
import os
import sqlite3

import pytest

from app import config

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ONT = os.path.join(ROOT, "ontology")
ANN = os.path.join(ONT, "annotations")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ONT, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


la = _load("load_annotations_rt", "load_annotations.py")
ck = _load("check_annotation_pages_rt", "check_annotation_pages.py")


def _open():
    if not os.path.exists(config.DB_PATH):
        return None
    uri = "file:" + config.DB_PATH.replace("\\", "/") + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
        db.execute("SELECT 1 FROM pages LIMIT 1")
        return db
    except sqlite3.Error:
        return None


@pytest.fixture(scope="module")
def db():
    conn = _open()
    if conn is None:
        pytest.skip("нет index.db")
    yield conn
    conn.close()


def _files():
    return sorted(glob.glob(os.path.join(ANN, "*.md")))


def _slug(path):
    return os.path.splitext(os.path.basename(path))[0]


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def test_all_files_marked_and_in_range(db):
    files = _files()
    assert len(files) >= 23
    errs = []
    for path in files:
        slug = _slug(path)
        text = _read(path)
        if not la.has_marker(text):
            errs.append(f"{slug}: нет маркера")
        row = db.execute("SELECT num_pages FROM sources WHERE slug=?", (slug,)).fetchone()
        assert row is not None, slug
        _, chs = la.parse(text)
        errs += [f"{slug}: {m}" for m in la.validate_chapters(chs, row[0])]
    assert errs == []


SAMPLE = [
    ("streptococcosis_dissertation", "Глава 1. Обзор литературы", 9, "обзор литературы"),
    ("anemia_and_drugs_used_in_treatment", "Введение", 3, "введение"),
    ("antimicrobial_therapy_handbook", "Tetracyclines", 260, "tetracycline"),
    ("avian_pathology", "Newcastle Disease", 128, "newcastle"),
    ("diseases_of_poultry", "Salmonella Infections", 746, "salmonella"),
    ("pathological_diagnostics_diseases_pigs", "3.41", 78, "стрептококкоз"),
    ("birds_biology_and_pathology", "Этология птицы", 199, "этология"),
    ("peisak_disease_of_pigs", "Вирусные болезни", 54, "вирусные болезни"),
    ("practical_guide_to_broiler_health_management", "Кокцидиоз", 291, "кокцидиоз"),
    ("modern_pig_farming", "Оборудование свиноводческих ферм", 0, None),
    ("vic_articles_swine", "Актуальность респираторных", 0, "респиратор"),
    ("avian_biocheck", "Ожидаемые титры", 1, "титр"),
    ("atrophic_rhinitis_of_pigs", "Введение", 1, "введение"),
]


def _chapter(slug, sub):
    _, chs = la.parse(_read(os.path.join(ANN, slug + ".md")))
    m = [(t, p, e) for t, p, e, _ in chs if sub in t]
    assert m, f"{slug}: нет главы «{sub}»"
    return m[0]


@pytest.mark.parametrize("slug,sub,expected,needle", SAMPLE)
def test_sample_chapter_start(db, slug, sub, expected, needle):
    _, page, _ = _chapter(slug, sub)
    assert page == expected
    if needle:
        sid = db.execute("SELECT id FROM sources WHERE slug=?", (slug,)).fetchone()[0]
        md = db.execute("SELECT markdown FROM pages WHERE source_id=? AND page_index=?",
                        (sid, page)).fetchone()[0]
        assert needle in (md or "").lower()   # lower в Python (кириллица)


def test_loader_position_is_argmax_per_book(db):
    """То же правило, что `check_annotation_pages.py --strict`: при n ≥ 3 вердикт строго
    «1-based» (ничья/все нули — ошибка); при n < 3 «неясно» допустимо, 0-based — нет."""
    bad = []
    for path in _files():
        res = ck.check_book(db, _slug(path), _read(path))
        assert res is not None, _slug(path)
        st, why = ck.strict_status(res)
        if st == "error":
            bad.append((_slug(path), why, res["basis"]))
    assert bad == []


def test_ontology_fts_matches_files(db):
    try:
        stale = db.execute(
            "SELECT 1 FROM ontology_fts WHERE source_slug='modern_pig_farming' AND page_index=-1"
        ).fetchone()
    except sqlite3.OperationalError:
        pytest.skip("нет ontology_fts")
    if stale:
        pytest.skip("индекс устарел: перезапусти ontology/load_annotations.py")
    for slug, sub, expected, _ in SAMPLE:
        title, _, _ = _chapter(slug, sub)
        row = db.execute(
            "SELECT page_index FROM ontology_fts WHERE kind='chapter' AND source_slug=? AND chapter_title=?",
            (slug, title),
        ).fetchone()
        assert row is not None, (slug, title)
        assert row[0] == expected, (slug, title, row[0])
