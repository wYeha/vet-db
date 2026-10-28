"""Проверка нумерации страниц в аннотациях (ontology/annotations/<slug>.md) по реальному
тексту страниц в data/index.db (read-only).

Для каждой главы с `стр A[–B]` ищем название главы в `pages.markdown` на страницах
p ∈ {A-2, A-1, A, A+1} (page_index). Позиция загрузчика — A-1 (конвенция 1-based).
По книге считаем, на какой позиции название находится чаще всего:
  * argmax = A-1  → файл в 1-based (как ожидает загрузчик);
  * argmax = A    → файл в 0-based (старт главы в индексе на 1 стр. раньше).

Запуск:
  python ontology/check_annotation_pages.py            # отчёт-таблица
  python ontology/check_annotation_pages.py --strict   # exit 1, если нет маркера или вердикт книги
                                                       # не «1-based» (при n ≥ 3 глав; «неясно»
                                                       # при n < 3 — только предупреждение)
"""
import argparse
import glob
import importlib.util
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.abspath(os.path.join(HERE, "..", "data", "index.db"))
ANN = os.path.join(HERE, "annotations")

_spec = importlib.util.spec_from_file_location("load_annotations", os.path.join(HERE, "load_annotations.py"))
_la = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_la)
parse, norm = _la.parse, _la.norm
has_marker = _la.has_marker

# Смещения относительно A (номер из файла): p = A + off. Позиция загрузчика — off = -1.
OFFSETS = (-2, -1, 0, 1)
LOADER_OFF = -1
FOUND_SHARE = 0.6

_PREFIX_RE = re.compile(
    r"^\s*(?:(?:глава|chapter|раздел|часть|part|section)\s*[\dIVX]+[.:]?\s*|[\dIVX]+(?:[.\-][\dIVX]+)*[.)]?\s+|[A-Za-zА-Яа-яЁё][.)]\s+)",
    re.I)


def title_tokens(title):
    """Токены названия главы без нумерации («Глава 1.», «3.41», «А.»), norm, длина ≥ 4."""
    t = title or ""
    prev = None
    while prev != t:            # «Глава 2. 2.1 Материалы» — снимаем префиксы повторно
        prev = t
        t = _PREFIX_RE.sub("", t, count=1)
    return [w for w in norm(t).split() if len(w) >= 4]


def heading_lines(md):
    """Только строки-заголовки markdown (`#…`) страницы."""
    return "\n".join(l for l in (md or "").splitlines() if l.lstrip().startswith("#"))


def title_found(md, title, share=FOUND_SHARE, headings_only=False):
    """True, если в тексте страницы (или только в её `#`-заголовках) есть ≥ share токенов
    названия (lower в Python)."""
    toks = title_tokens(title)
    if headings_only:
        md = heading_lines(md)
    if not toks or not md:
        return False
    low = norm(md)
    hit = sum(1 for w in toks if w in low)
    return hit / len(toks) >= share


def book_verdict(counts):
    """counts: {off: число глав, где название найдено на A+off}.
    → ('1-based' | '0-based' | 'неясно', argmax_offsets)."""
    if not counts or max(counts.values()) == 0:
        return "неясно", []
    best = max(counts.values())
    arg = sorted(o for o, v in counts.items() if v == best)
    if arg == [-1]:
        return "1-based", arg
    if arg == [0]:
        return "0-based", arg
    return "неясно", arg


def loader_is_argmax(counts):
    """Позиция загрузчика (A-1) — нестрогий argmax счётчиков (ничья проходит).
    Для --strict используйте `strict_status` — там ничья/нули при n ≥ 3 — ошибка."""
    return counts[LOADER_OFF] >= max(counts.values())


MIN_CHAPTERS_STRICT = 3
BASIS_HEADINGS = "#-заголовки"
BASIS_FULLTEXT = "текст(fallback)"


def strict_status(res):
    """Статус книги для --strict → ('ok' | 'warn' | 'error', причина).
    error: нет маркера; вердикт «0-based»; вердикт «неясно» (ничья или все нули) при
    n ≥ 3 глав. warn: «неясно» при n < 3. ok: вердикт «1-based» (A-1 — единственный argmax)."""
    if not res["marker"]:
        return "error", "нет маркера"
    v = res["verdict"]
    if v == "1-based":
        return "ok", ""
    if v == "0-based":
        return "error", "вердикт 0-based (позиция загрузчика A-1 не argmax)"
    if res["n"] >= MIN_CHAPTERS_STRICT:
        return "error", f"вердикт неясен при n={res['n']} (ничья/нет совпадений: {res['counts']})"
    return "warn", f"вердикт неясен при n={res['n']} < {MIN_CHAPTERS_STRICT}"


def chapter_verdict(found):
    """found: {off: bool}. Однозначный вердикт по главе или None."""
    a, b = found.get(-1), found.get(0)
    if a and not b:
        return "1-based"
    if b and not a:
        return "0-based"
    return None


def open_ro(path=DB):
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def check_book(cx, slug, text):
    """→ dict: n, counts, verdict, argmax, mismatches, marker, num_pages, basis.

    Сначала ищем название только в `#`-заголовках страниц (колонтитулы вида «ГЛАВА 2
    Оптимальный рост» на следующих страницах и оглавление тогда не мешают). Если по
    заголовкам вердикт неясен — считаем по всему тексту страницы."""
    row = cx.execute("SELECT id, num_pages FROM sources WHERE slug=?", (slug,)).fetchone()
    if row is None:
        return None
    sid, num_pages = row
    _, chapters = parse(text)
    counts_h = {o: 0 for o in OFFSETS}
    counts_f = {o: 0 for o in OFFSETS}
    per = []
    n = 0
    for title, page, _end, _s in chapters:
        if page is None:
            continue
        a = page + 1                      # A из файла
        n += 1
        fh, ff = {}, {}
        for o in OFFSETS:
            p = a + o
            r = cx.execute("SELECT markdown FROM pages WHERE source_id=? AND page_index=?", (sid, p)).fetchone()
            fh[o] = bool(r) and title_found(r[0], title, headings_only=True)
            ff[o] = bool(r) and title_found(r[0], title)
            counts_h[o] += fh[o]
            counts_f[o] += ff[o]
        per.append((title, a, fh, ff))
    verdict, arg = book_verdict(counts_h)
    basis, counts = BASIS_HEADINGS, counts_h
    if verdict == "неясно":
        verdict, arg = book_verdict(counts_f)
        basis, counts = BASIS_FULLTEXT, counts_f
    mism = []
    if verdict in ("1-based", "0-based"):
        for title, a, fh, ff in per:
            cv = chapter_verdict(fh if basis == BASIS_HEADINGS else ff)
            if cv and cv != verdict:
                mism.append((title, a, cv))
    return dict(n=n, counts=counts, basis=basis, verdict=verdict, argmax=arg, mismatches=mism,
                marker=has_marker(text), num_pages=num_pages)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--db", default=DB)
    ap.add_argument("-v", "--verbose", action="store_true", help="печатать главы с расхождением")
    args = ap.parse_args(argv)
    cx = open_ro(os.path.abspath(args.db))
    bad, warn = [], []
    print(f"{'slug':48} {'n':>3} {'A-2':>4} {'A-1*':>4} {'A':>4} {'A+1':>4}  вердикт   {'по':15} маркер  расхожд.")
    for path in sorted(glob.glob(os.path.join(ANN, "*.md"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        res = check_book(cx, slug, text)
        if res is None:
            print(f"{slug:48} ! нет источника")
            bad.append(slug)
            continue
        c = res["counts"]
        print(f"{slug:48} {res['n']:>3} {c[-2]:>4} {c[-1]:>4} {c[0]:>4} {c[1]:>4}  {res['verdict']:9} {res['basis']:15} "
              f"{'да' if res['marker'] else 'нет':6}  {len(res['mismatches'])}")
        if args.verbose:
            for title, a, cv in res["mismatches"]:
                print(f"    ~ {cv}: «{title}» (стр {a})")
        st, why = strict_status(res)
        if st == "error":
            bad.append(f"{slug}: {why}")
        elif st == "warn":
            warn.append(f"{slug}: {why}")
    cx.close()
    print(f"\nСтолбец «по»: {BASIS_HEADINGS} — вердикт по #-заголовкам страниц; "
          f"{BASIS_FULLTEXT} — по заголовкам неясно, посчитано по всему тексту страницы.")
    if warn:
        print("\nПредупреждения:")
        for w in warn:
            print("  ? " + w)
    if bad:
        print("\nНе прошли:")
        for b in bad:
            print("  ! " + b)
    if args.strict and bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
