"""Загрузка смысловых аннотаций (ontology/annotations/<slug>.md) в index.db:
 - sources.description  ← аннотация книги;
 - toc.summary          ← аннотация главы (best-effort по совпадению названия);
 - ontology_fts         ← FTS по аннотациям (книга + главы) для поиска.

Идемпотентно. Работает по существующему index.db (S3 не нужен).
Запуск:  python ontology/load_annotations.py
"""
import sqlite3, re, os, glob

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "data", "index.db")
ANN = os.path.join(HERE, "annotations")

def norm(s):
    s = (s or "").lower()
    s = re.sub(r'[^0-9a-zа-яё ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def parse(text):
    """→ (book_annotation, [(chapter_title, page_index|None, page_end|None, summary), ...])

    Заголовок главы: `### Название (стр A–B)` / `(стр. A)` / `(Глава 1, стр 3–18)`;
    тире `–`, `—` или `-`. page_index = A-1, page_end = B (raw) — это exclusive-конец
    в той же конвенции (inclusive последняя стр. = B-1).
    ВНИМАНИЕ: конвенция A в аннотациях смешанная (где-то A уже 0-based, где-то
    1-based), поэтому page_index/page_end ошибаются на ±1 стр.; рантайм гасит это
    запасом диапазона (backend/app/ontology.py::_RANGE_MARGIN). Здесь не исправляем.
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
        page = int(pm.group(1)) - 1 if pm else None       # «1-based» показ → page_index (best-effort, ±1)
        page_end = int(pm.group(2)) if pm and pm.group(2) else None   # raw B = exclusive-конец
        title = re.sub(r'\s*\(.*$', '', head).strip()      # убрать "(стр A–B)"
        if title and body:
            chapters.append((title, page, page_end, body))
    return book, chapters

def main():
    cx = sqlite3.connect(DB)
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
    for path in sorted(glob.glob(os.path.join(ANN, "*.md"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        sid = slug2id.get(slug)
        if sid is None:
            print("  ! нет источника для", slug); continue
        book, chapters = parse(open(path, encoding="utf-8").read())
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

if __name__ == "__main__":
    main()
