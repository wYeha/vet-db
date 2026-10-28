"""Загрузка смысловых аннотаций (ontology/annotations/<slug>.md) в index.db:
 - sources.description  ← аннотация книги;
 - toc.summary          ← аннотация главы (best-effort по совпадению названия);
 - ontology_fts         ← FTS по аннотациям (книга + главы) для поиска.

Идемпотентно. Работает по существующему index.db (S3 не нужен).
Запуск:  python ontology/load_annotations.py [--strict]
  --strict — если есть предупреждения (нет маркера конвенции, страницы вне книги), exit 1
             ДО любых записей в БД (индекс не меняется).
"""
import argparse, sqlite3, re, os, glob, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "data", "index.db")
ANN = os.path.join(HERE, "annotations")
# Маркер конвенции страниц — первая строка каждого файла аннотации.
MARKER = "<!-- pages: 1-based; стр A–B = page_index+1, B включительно -->"
MARKER_RE = re.compile(r"<!--\s*pages:\s*1-based")

def norm(s):
    s = (s or "").lower()
    s = re.sub(r'[^0-9a-zа-яё ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def parse(text):
    """→ (book_annotation, [(chapter_title, page_index|None, page_end|None, summary), ...])

    Заголовок главы: `### Название (стр A–B)` / `(стр. A)` / `(Глава 1, стр 3–18)`;
    тире `–`, `—` или `-`.
    Конвенция (единая для всех файлов, маркер первой строкой — см. MARKER): A — первая
    страница главы, B — последняя страница включительно, обе 1-based, как в читалке
    (= page_index + 1). Отсюда page_index = A-1, page_end = B (raw): inclusive 1-based
    последняя страница численно равна exclusive 0-based концу.
    Проверка по тексту страниц: ontology/check_annotation_pages.py.
    """
    book = ""
    m = re.search(r'##\s*Книга\s*\n(.*?)(?=\n##\s|\Z)', text, re.S)
    if m:
        book = m.group(1).strip()
    chapters = []
    for cm in re.finditer(r'^###\s+(.+?)[ \t]*\n(.*?)(?=^###\s|\Z)', text, re.S | re.M):
        head = cm.group(1).strip()
        body = re.sub(r'<!--.*?-->', '', cm.group(2), flags=re.S).strip()
        pm = re.search(r'стр\.?\s*(\d+)(?:\s*[–—-]\s*(\d+))?', head)
        page = int(pm.group(1)) - 1 if pm else None       # A (1-based) → page_index (0-based)
        page_end = int(pm.group(2)) if pm and pm.group(2) else None   # B (1-based, incl) = exclusive 0-based конец
        title = re.sub(r'\s*\(.*$', '', head).strip()      # убрать "(стр A–B)"
        if title and body:
            chapters.append((title, page, page_end, body))
    return book, chapters

def has_marker(text):
    """Маркер конвенции страниц в первой строке файла."""
    return bool(MARKER_RE.search((text or "").split("\n", 1)[0]))

def validate_chapters(chapters, num_pages):
    """Проверка диапазонов глав из parse() → список сообщений (пусто = ок).
    Перекрытия глав не запрещаются."""
    msgs = []
    for title, page, page_end, _summ in chapters:
        if page is None:
            continue
        t = title[:60]
        if page < 0:
            msgs.append(f"«{t}»: page_index={page} < 0")
        if num_pages is not None and page >= num_pages:
            msgs.append(f"«{t}»: page_index={page} >= num_pages={num_pages}")
        if page_end is not None:
            if num_pages is not None and page_end > num_pages:
                msgs.append(f"«{t}»: page_end={page_end} > num_pages={num_pages}")
            if page_end <= page:
                msgs.append(f"«{t}»: page_end={page_end} <= page_index={page}")
    return msgs

def _ann_files(ann_dir):
    return sorted(glob.glob(os.path.join(ann_dir, "*.md")))

def collect_warnings(cx, ann_dir=ANN):
    """Проверка всех файлов аннотаций (маркер + validate_chapters) — только чтение БД."""
    npages_by_slug = {slug: n for slug, n in cx.execute("SELECT slug, num_pages FROM sources")}
    warns = []
    for path in _ann_files(ann_dir):
        slug = os.path.splitext(os.path.basename(path))[0]
        if slug not in npages_by_slug:
            continue                      # «нет источника» печатается при загрузке
        text = open(path, encoding="utf-8").read()
        _, chapters = parse(text)
        if not has_marker(text):
            warns.append(f"{slug}: нет маркера конвенции страниц первой строкой ({MARKER})")
        warns += [f"{slug}: {m}" for m in validate_chapters(chapters, npages_by_slug[slug])]
    return warns

def _print_warns(warns):
    print(f"\nПредупреждения ({len(warns)}):")
    for w in warns:
        print("  ! " + w)

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="при предупреждениях exit 1 ДО любых записей в БД")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--ann", default=ANN)
    args = ap.parse_args(argv)
    # sqlite3.connect молча создаёт пустой файл по неверному пути — проверяем заранее
    if not os.path.exists(args.db):
        sys.exit(f"нет индекса: {args.db} (сначала соберите его ingest'ом)")
    cx = sqlite3.connect(args.db)
    # 0) проверка файлов ДО записи: в --strict плохие данные в индекс не попадают
    warns = collect_warnings(cx, args.ann)
    if warns and args.strict:
        cx.close()
        _print_warns(warns)
        print("--strict: индекс не изменён.")
        sys.exit(1)
    # 1) колонка toc.summary
    cols = [r[1] for r in cx.execute("PRAGMA table_info(toc)")]
    if "summary" not in cols:
        cx.execute("ALTER TABLE toc ADD COLUMN summary TEXT")
    # 2) FTS по аннотациям
    cx.executescript("""
    DROP TABLE IF EXISTS ontology_fts;
    CREATE VIRTUAL TABLE ontology_fts USING fts5(
        chapter_title, summary,
        source_id UNINDEXED, source_slug UNINDEXED, source_title UNINDEXED,
        page_index UNINDEXED, kind UNINDEXED,
        page_end UNINDEXED,   -- в конце: индексы колонок для snippet() не сдвигаются
        tokenize='unicode61');
    """)
    slug2id = {slug: sid for slug, sid in cx.execute("SELECT slug, id FROM sources")}
    title_by_id = {i: t for i, t in cx.execute("SELECT id, title FROM sources")}
    nb = nch = 0
    for path in _ann_files(args.ann):
        slug = os.path.splitext(os.path.basename(path))[0]
        sid = slug2id.get(slug)
        if sid is None:
            print("  ! нет источника для", slug); continue
        text = open(path, encoding="utf-8").read()
        book, chapters = parse(text)
        stitle = title_by_id[sid]
        if book:
            cx.execute("UPDATE sources SET description=? WHERE id=?", (book, sid))
            cx.execute("INSERT INTO ontology_fts(chapter_title,summary,source_id,source_slug,source_title,page_index,kind,page_end) "
                       "VALUES('',?,?,?,?,NULL,'book',NULL)", (book, sid, slug, stitle))
            nb += 1
        # курированные toc для best-effort привязки summary
        cur = cx.execute("SELECT id,title FROM toc WHERE source_id=? AND origin='curated'", (sid,)).fetchall()
        curmap = {norm(t): tid for tid, t in cur}
        for title, page, page_end, summ in chapters:
            cx.execute("INSERT INTO ontology_fts(chapter_title,summary,source_id,source_slug,source_title,page_index,kind,page_end) "
                       "VALUES(?,?,?,?,?,?,'chapter',?)", (title, summ, sid, slug, stitle, page, page_end))
            tid = curmap.get(norm(title))
            if tid:
                cx.execute("UPDATE toc SET summary=? WHERE id=?", (summ, tid))
            nch += 1
        print(f"  [{slug:46}] книга={'y' if book else '-'} глав={len(chapters)}")
    cx.commit()
    tot = cx.execute("SELECT COUNT(*) FROM ontology_fts").fetchone()[0]
    print(f"\nЗагружено: описаний книг={nb}, аннотаций глав={nch}, строк ontology_fts={tot}")
    cx.close()
    if warns:
        _print_warns(warns)
    else:
        print("Предупреждений нет.")

if __name__ == "__main__":
    main()
