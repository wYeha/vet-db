"""Юнит-тесты построения FTS5 MATCH-строк (без БД): префикс + грубая обрезка."""
import re

import pytest

from app.db import _stem, build_match_query, build_or_match_query


@pytest.mark.parametrize("word,stem", [
    ("слон", "слон"),            # 4 — не обрезаем
    ("слона", "слон"),           # 5 — срезаем 1
    ("слонов", "слоно"),         # 6 — срезаем 1
    ("слоновый", "слонов"),      # 8 — срезаем 2
    ("стрептококкоз", "стрептококк"),
    ("abcd", "abcd"),
    ("tylosin", "tylos"),        # 7 — срезаем 2
    ("Newcastle", "newcast"),
    ("Streptococcus", "Streptococc".lower()),
])
def test_stem_lengths(word, stem):
    assert _stem(word) == stem


def test_stem_boundaries_7():
    assert _stem("абвгдеж") == "абвгд"  # 7 → -2
    assert _stem("абвгде") == "абвгд"   # 6 → -1


def test_digits_and_short_are_exact():
    assert build_match_query("10 мг") == '"10" "мг"'
    assert build_match_query("100") == '"100"'


def test_mixed_digit_letters_not_cut_but_prefixed():
    assert build_match_query("B12") == '"b12"*'
    assert build_match_query("H5N1") == '"h5n1"*'


def test_case_and_prefix_on_every_token():
    assert build_match_query("Ньюкасл Newcastle") == '"ньюка"* "newcast"*'


def test_dedup_after_stem():
    assert build_match_query("слона слон Слон") == '"слон"*'


def test_and_vs_or():
    assert " OR " not in build_match_query("тилозин tylosin")
    assert build_or_match_query("тилозин tylosin") == '"тилоз"* OR "tylos"*'


def test_or_cap_on_unique_terms():
    q = "aaaaaa bbbbbb cccccc dddddd eeeeee ffffff gggggg"
    assert build_or_match_query(q).count(" OR ") == 5
    assert build_or_match_query(q, max_tokens=2).count(" OR ") == 1
    assert build_or_match_query("слон слона слон", max_tokens=1) == '"слон"*'


@pytest.mark.parametrize("fn", [build_match_query, build_or_match_query])
def test_special_chars_sanitized(fn):
    out = fn('"тилозин" * : - ( ) ^ \'x\' абвг')
    for ch in '*:-()^\'':
        if ch == "*":
            continue  # `*` допустим только как суффикс терма (проверяется regex ниже)
        assert ch not in out
    sep = " OR " if fn is build_or_match_query else " "
    for term in out.split(sep):
        assert re.fullmatch(r'"[^"]+"\*?', term), term


@pytest.mark.parametrize("fn", [build_match_query, build_or_match_query])
@pytest.mark.parametrize("q", ["", None, "   ", '"*:-()^', "---"])
def test_empty_and_garbage(fn, q):
    assert fn(q) == ""


def test_long_input():
    q = " ".join(f"слово{i}" for i in range(500))
    assert build_match_query(q).count('"') == 1000
    assert build_or_match_query(q).count(" OR ") == 5


def test_yo_not_normalized():
    # фактическое поведение: «ё» остаётся как есть, «ё»/«е» не нормализуются
    assert build_match_query("Ёлка") == '"ёлка"*'
    assert build_match_query("Елка") == '"елка"*'


def test_or_token_is_quoted_literal():
    # «or» (2 символа) — точный квотированный токен, не оператор FTS5; префикса нет (<3)
    assert build_match_query("or") == '"or"'
    assert build_or_match_query("or") == '"or"'
