"""Юнит-тесты безопасных сниппетов и чистки <br>."""
import pytest

from app import ontology
from app.snippets import _MARK_CLOSE, _MARK_OPEN, safe_snippet

O, C = _MARK_OPEN, _MARK_CLOSE


@pytest.mark.parametrize("raw", ["a<br>b", "a<br/>b", "a<br />b", "a<BR>b", "a<BR />b", "a< br >b"])
def test_br_forms_become_space(raw):
    out = safe_snippet(raw)
    assert out == "a b"
    assert "&lt;br" not in out


def test_adjacent_br_collapse():
    assert safe_snippet("x<br><br>y") == "x y"
    assert safe_snippet("x <br> y") == "x y"


def test_marks_preserved():
    assert safe_snippet(f"a<br>{O}тилозин{C}") == "a <mark>тилозин</mark>"


def test_other_html_escaped():
    out = safe_snippet("<img src=x onerror=alert(1)><script>x</script><br onclick=x>")
    assert "<img" not in out and "<script" not in out and "<br" not in out
    assert "&lt;img" in out and "&lt;script&gt;" in out and "&lt;br onclick=x&gt;" in out


def test_none():
    assert safe_snippet(None) is None


def test_edge_fragments():
    assert safe_snippet("br> текст") == "текст"
    assert safe_snippet("…br> текст") == "…текст"
    assert safe_snippet("текст <br") == "текст"
    assert safe_snippet("текст </br") == "текст"


def test_ontology_row_same_cleaning():
    r = {"source_id": 1, "source_slug": "s", "source_title": "t", "chapter_title": "c",
         "page_index": 1, "kind": "chapter", "rank": -1.0,
         "snip": "a<br>\x02тилозин\x03<br/>b <img src=x>"}
    out = ontology._row(r)["snippet"]
    assert out == "a <mark>тилозин</mark> b &lt;img src=x&gt;"
    assert "&lt;br" not in out
