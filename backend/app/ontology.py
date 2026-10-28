"""Ядро поиска по смысловым аннотациям (онтологии): FTS по ontology_fts.

Аннотации — сгенерированный Claude/субагентами «смысловой» слой над книгами
(описание книги + аннотации глав). Поиск как обычный FTS: сначала строгий AND,
при малом числе хитов — добор OR (как в чате). Сниппет безопасен (html-
экранирование + только доверенный <mark>).

Модуль зависит только от `db.py`/`snippets.py`/`schemas.py` (без роутеров),
поэтому его переиспользуют потребители без дублирования SQL и без импорта роутера
из роутера:
- REST-роутер `/api/ontology/search` (внешний скилл) — только `search_ontology`,
  контракт неизменен;
- чат `mode=ontology` — `search_ontology` (этап 1: книга/глава + старт), затем
  `refine_ontology_hits` (этап 2): FTS по `pages_fts` внутри диапазона страниц
  главы → плашка ведёт на лучшую по BM25 страницу со сниппетом из тела книги
  (`precise=True`); если в диапазоне пусто — фолбэк на старт главы и сниппет
  аннотации (`precise=False`). Новых LLM-вызовов нет;
- inline-чат (`mode=None`) — `search_ontology` как ориентир для модели.
"""
import html
import re
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .db import build_match_query, build_or_match_query
from .schemas import SearchHit
from .snippets import SNIPPET_ARGS, safe_snippet

log = logging.getLogger("vetai.ontology")

_SQL = (
    "SELECT source_id, source_slug, source_title, chapter_title, page_index, kind, "
    "snippet(ontology_fts, 1, char(2), char(3), '…', 20) AS snip, rank "
    "FROM ontology_fts WHERE ontology_fts MATCH ? ORDER BY rank LIMIT ?"
)

# Порог «строгих» хитов, ниже которого домешиваем OR-выдачу.
_MIN_STRONG_HITS = 3


def _row(r) -> dict:
    snip = html.escape(r["snip"] or "").replace(chr(2), "<mark>").replace(chr(3), "</mark>")
    return {
        "source_id": r["source_id"],
        "source_slug": r["source_slug"],
        "source_title": r["source_title"],
        "chapter_title": r["chapter_title"],
        "page_index": r["page_index"],
        "kind": r["kind"],
        "snippet": snip,
        "score": r["rank"],
    }


def _run(db, match: str, limit: int) -> List[dict]:
    if not match:
        return []
    return [_row(r) for r in db.execute(_SQL, (match, limit)).fetchall()]


def search_ontology(db, q: str, limit: int = 20) -> List[dict]:
    """Поиск по онтологии по готовому соединению `db`.

    Сначала строгий AND; при нехватке строгих хитов (< _MIN_STRONG_HITS) —
    добор OR-выдачи с дедупом по `(source_id, chapter_title, kind)`.
    Возвращает список dict-хитов (тот же контракт, что у `/api/ontology/search`).
    """
    hits = _run(db, build_match_query(q), limit)
    if len(hits) < _MIN_STRONG_HITS:
        seen = {(h["source_id"], h["chapter_title"], h["kind"]) for h in hits}
        for h in _run(db, build_or_match_query(q), limit):
            key = (h["source_id"], h["chapter_title"], h["kind"])
            if key not in seen:
                hits.append(h)
                seen.add(key)
            if len(hits) >= limit:
                break
    return hits[:limit]


# --- Этап 2: от начала главы к конкретной странице-фрагменту -----------------

# Запас диапазона с обеих сторон: конвенция стр. в аннотациях смешанная (0/1-based),
# отсюда систематическая ошибка старта ±1 стр.
_RANGE_MARGIN = 1


def _chapter_ranges(db, source_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """Диапазоны глав по источникам: PRAGMA + 2 запроса на весь вызов (без N+1).

    → {source_id: {"chapters": [(start, end_excl|None, title)] (по start),
                   "num_pages": n|None}}

    end_excl: `page_end` (колонка есть и не NULL; хранится B = exclusive-конец в
    конвенции старта A-1), иначе минимальный старт другой главы строго больше
    текущего, иначе `sources.num_pages`, иначе None (верхней границы нет).
    Если end_excl <= start → start + 1. Главы без старта в диапазоны не входят.
    """
    ids = sorted({int(s) for s in source_ids if s is not None})
    out: Dict[int, Dict[str, Any]] = {sid: {"chapters": [], "num_pages": None} for sid in ids}
    if not ids:
        return out
    ph = ",".join("?" * len(ids))
    raw: Dict[int, list] = {sid: [] for sid in ids}
    # колонка page_end есть только в пересобранном индексе — проверяем схему явно;
    # колонки перечисляем (summary не тянем)
    has_end = "page_end" in {r[1] for r in db.execute("PRAGMA table_info(ontology_fts)")}
    end_col = "page_end" if has_end else "NULL AS page_end"
    for r in db.execute(
        f"SELECT source_id, chapter_title, page_index, {end_col} FROM ontology_fts "
        f"WHERE kind='chapter' AND source_id IN ({ph})", ids
    ).fetchall():
        if r["page_index"] is None:
            continue
        page_end = r["page_end"]
        raw.setdefault(int(r["source_id"]), []).append(
            (int(r["page_index"]), None if page_end is None else int(page_end), r["chapter_title"])
        )
    for r in db.execute(f"SELECT id, num_pages FROM sources WHERE id IN ({ph})", ids).fetchall():
        out[int(r["id"])]["num_pages"] = r["num_pages"]
    for sid, chs in raw.items():
        chs.sort(key=lambda c: c[0])
        starts = [c[0] for c in chs]
        num_pages = out[sid]["num_pages"]
        ranges = []
        for start, page_end, title in chs:
            if page_end is not None:
                end = page_end
            else:
                nxt = [s for s in starts if s > start]  # строго больший старт
                end = min(nxt) if nxt else num_pages
            if end is not None and end <= start:
                end = start + 1
            ranges.append((start, end, title))
        out[sid]["chapters"] = ranges
    return out


# --- Детектор страниц списка литературы ---------------------------------------
# Плотная латынь/термины в библиографии выигрывают BM25 у основного текста главы,
# поэтому такие страницы не выбираем как «точный фрагмент». Эвристика по строкам:
# строка-цитата = есть год (1800–2039) И строка начинается как библиографическая
# запись (Harvard/Vancouver/«И.О. Фамилия»/«Орг, 2014.»/«Орг (2019)», опционально
# с номером/маркером). Страница — библиография, если таких строк ≥3 и ≥50%
# среди содержательных (≥25 символов). Пороги проверены на всех страницах pages_fts.
_YEAR = re.compile(r'(?<!\d)(?:1[89]\d{2}|20[0-3]\d)[a-zа-я]?(?!\d)')
_U = r"[A-ZА-ЯЁ]"
_SUR = r"(?:(?:van|von|de|der|den|da|di|le|la)\s+)*[A-ZА-ЯЁ][\w'’\-]+"
_ENTRY = re.compile(rf'''^\s*(?:[-*•]\s*)?(?:\[?\d{{1,4}}[\].)]?\s*)?(?:
     {_SUR},?\s+(?:{_U}\.\s?-?){{1,3}}                              # Фамилия, И.О. / Alexander, D.J.
   | {_SUR}\s+{_U}{{1,3}}(?:,|\.|\s+et\s+al)                        # Vancouver
   | (?:{_U}\.\s?){{1,3}}\s*{_SUR}                                  # И.О. Фамилия
   | {_SUR}(?:,\s*{_SUR}){{0,3}},?\s+(?:1[89]|20)\d{{2}}[a-z]?[.,] # AMCRA, 2014.
   | {_U}[\w&.,'’\- ]{{1,60}}?\(\s*(?:1[89]|20)\d{{2}}[a-z]?\s*\)  # OIE (2019)
)''', re.X)


def _is_bibliography(md: Optional[str]) -> bool:
    """True, если страница — список литературы (см. эвристику выше)."""
    lines = [l for l in (md or "").splitlines() if len(l.strip()) >= 25]
    if not lines:
        return False
    cit = sum(1 for l in lines if _YEAR.search(l) and _ENTRY.match(l))
    return cit >= 3 and cit / len(lines) >= 0.5


# Сколько лучших по BM25 кандидатов просматривать в диапазоне (глава sid 9 — 58 стр.,
# из них литература — 23): страницы библиографии пропускаются.
_CANDIDATES = 60


def _best_page(db, match: str, source_id: int, lo: Optional[int], hi: Optional[int],
               stats: Optional[Dict[str, int]] = None):
    """Лучшая по BM25 НЕ-библиографическая страница источника в [lo, hi)
    → (page_index, raw_snippet) | None.

    Один запрос (до `_CANDIDATES` кандидатов), классификация лениво — до первой
    не-библиографии. Если все кандидаты — литература, ступень пуста (None).
    `stats["bib_skipped"]` — счётчик отброшенных страниц (для лога).
    """
    sql = (
        f"SELECT page_index, markdown, snippet(pages_fts, 0, {SNIPPET_ARGS}) AS snip "
        "FROM pages_fts WHERE pages_fts MATCH ? AND source_id = ?"
    )
    args: list = [match, source_id]
    if lo is not None:
        sql += " AND page_index >= ?"
        args.append(lo)
    if hi is not None:
        sql += " AND page_index < ?"
        args.append(hi)
    sql += " ORDER BY rank LIMIT ?"
    args.append(_CANDIDATES)
    for r in db.execute(sql, args):
        if _is_bibliography(r["markdown"]):
            if stats is not None:
                stats["bib_skipped"] = stats.get("bib_skipped", 0) + 1
            continue
        return r["page_index"], r["snip"]
    return None


def _shift_fallback_start(db, source_id: int, page: int) -> int:
    """Старт главы из аннотации может (из-за ±1) указывать на литературу ПРЕДЫДУЩЕЙ
    главы: если стр. `page` — библиография, а `page+1` нет → `page+1`.
    Один точечный запрос, вызывается только для фолбэк-хитов."""
    pages = {
        r["page_index"]: r["markdown"]
        for r in db.execute(
            "SELECT page_index, markdown FROM pages_fts "
            "WHERE source_id = ? AND page_index IN (?, ?)",
            (source_id, page, page + 1),
        )
    }
    if page in pages and (page + 1) in pages \
            and _is_bibliography(pages[page]) and not _is_bibliography(pages[page + 1]):
        return page + 1
    return page


def fallback_hit(o: Dict[str, Any]) -> SearchHit:
    """Строка онтологии → SearchHit(type="page") как есть: старт главы (или уровень
    книги, page_index=None), сниппет аннотации, `precise=False`.

    В title — заголовок главы (или «о книге» для kind=book).
    """
    kind = o.get("kind")
    chapter = o.get("chapter_title")
    title = chapter if chapter else ("о книге" if kind == "book" else None)
    return SearchHit(
        type="page",
        source_id=o.get("source_id"),
        source_title=o.get("source_title"),
        page_index=o.get("page_index"),
        title=title,
        snippet=o.get("snippet"),
        score=o.get("score"),
        precise=False,
    )


_fallback_hit = fallback_hit  # совместимость со старым именем


def _chapter_for_page(chapters, page: int) -> Optional[str]:
    """Глава, в чей диапазон [start, end) попала страница (самая вложенная —
    с наибольшим стартом)."""
    best = None
    for start, end, title in chapters:
        if start <= page and (end is None or page < end):
            if best is None or start > best[0]:
                best = (start, title)
    return best[1] if best else None


def _end_for(chapters, start: int, title: Optional[str], num_pages) -> Optional[int]:
    """exclusive-конец главы со стартом `start` (по уже загруженным диапазонам)."""
    for s, e, t in chapters:
        if s == start and t == title:
            return e
    nxt = [c[0] for c in chapters if c[0] > start]
    end = min(nxt) if nxt else num_pages
    if end is not None and end <= start:
        end = start + 1
    return end


def _is_book_level(h: SearchHit) -> bool:
    return h.title is None or h.title == "о книге"


def refine_ontology_hits(db, rows: List[Dict[str, Any]], keywords: str) -> List[SearchHit]:
    """Этап 2: для каждой строки онтологии — FTS по `pages_fts` в диапазоне главы.

    Ступени запроса: AND (`build_match_query`), при пустом результате — OR
    (`build_or_match_query`). Страницы списка литературы (`_is_bibliography`)
    пропускаются; если в ступени только литература — она считается пустой.
    Найдено → SearchHit на лучшую страницу, сниппет из тела (безопасный
    `safe_snippet`), `precise=True`; иначе — фолбэк `fallback_hit` (старт главы +
    аннотация, `precise=False`; если старт — литература предыдущей главы, а
    следующая стр. нет — сдвиг на +1).
    kind=book и глава без старта — поиск по всему источнику (нижняя граница —
    первая известная глава минус запас), подпись — глава по диапазону.
    Дедуп по (source_id, page_index) в порядке онтологии; score = ontology score.
    Может бросить sqlite3.OperationalError (старый index.db) — ловит вызывающий.
    """
    if not rows:
        return []
    t0 = time.perf_counter()
    and_q = build_match_query(keywords or "")
    or_q = build_or_match_query(keywords or "")
    queries = [q for q in (and_q, or_q) if q]
    if len(queries) == 2 and queries[0] == queries[1]:
        queries = queries[:1]

    # пустые keywords → сразу фолбэк, без SQL
    ranges: Dict[int, Dict[str, Any]] = {}
    if queries:
        ranges = _chapter_ranges(db, {r.get("source_id") for r in rows})

    cache: Dict[tuple, Any] = {}
    shift_cache: Dict[tuple, int] = {}
    stats: Dict[str, int] = {"bib_skipped": 0}
    fts_calls = 0
    fallbacks = 0
    hits: List[SearchHit] = []
    by_key: Dict[tuple, SearchHit] = {}

    for row in rows:
        sid = row.get("source_id")
        hit: Optional[SearchHit] = None
        if queries and sid is not None:
            info = ranges.get(int(sid)) or {"chapters": [], "num_pages": None}
            chapters = info["chapters"]
            start = row.get("page_index")
            whole_source = row.get("kind") == "book" or start is None
            if whole_source:
                # отсекаем титул/печатное оглавление: от первой известной главы
                lo = max(0, chapters[0][0] - _RANGE_MARGIN) if chapters else None
                hi = None
            else:
                end = _end_for(chapters, start, row.get("chapter_title"), info["num_pages"])
                lo = max(0, start - _RANGE_MARGIN)
                hi = None if end is None else end + _RANGE_MARGIN
            found = None
            for q in queries:
                key = (q, sid, lo, hi)
                if key not in cache:
                    cache[key] = _best_page(db, q, sid, lo, hi, stats=stats)
                    fts_calls += 1
                found = cache[key]
                if found:
                    break
            if found:
                page, snip = found
                if whole_source:
                    title = _chapter_for_page(chapters, page)
                    if title is None and row.get("kind") != "book":
                        title = row.get("chapter_title")
                else:
                    title = row.get("chapter_title")
                hit = SearchHit(
                    type="page",
                    source_id=sid,
                    source_title=row.get("source_title"),
                    page_index=page,
                    title=title,
                    snippet=safe_snippet(snip),
                    score=row.get("score"),
                    precise=True,
                )
        if hit is None:
            hit = fallback_hit(row)
            fallbacks += 1
            # старт главы мог попасть на литературу предыдущей главы (±1) → +1
            if queries and hit.source_id is not None and hit.page_index is not None:
                skey = (hit.source_id, hit.page_index)
                if skey not in shift_cache:
                    shift_cache[skey] = _shift_fallback_start(db, hit.source_id, hit.page_index)
                hit.page_index = shift_cache[skey]

        k = (hit.source_id, hit.page_index)
        prev = by_key.get(k)
        if prev is None:
            by_key[k] = hit
            hits.append(hit)
        elif _is_book_level(prev) and row.get("kind") == "chapter" and hit.title:
            # book-хит совпал со страницей главы → подписываем главой
            prev.title = hit.title

    log.info(
        "ontology refine: rows=%d fts=%d fallbacks=%d bib_skipped=%d ms=%.0f",
        len(rows), fts_calls, fallbacks, stats["bib_skipped"],
        (time.perf_counter() - t0) * 1000.0,
    )
    return hits
