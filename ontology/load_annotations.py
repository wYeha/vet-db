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
    """→ (book_annotation, [(chapter_title, page_index|None, summary), ...])"""
    book = ""
    m = re.search(r'##\s*Книга\s*\n(.*?)(?=\n##\s|\Z)', text, re.S)
    if m:
        book = m.group(1).strip()
    chapters = []
    for cm in re.finditer(r'^###\s+(.+?)[ \t]*\n(.*?)(?=^###\s|\Z)', text, re.S | re.M):
        head = cm.group(1).strip()
        body = re.sub(r'<!--.*?-->', '', cm.group(2), flags=re.S).strip()
        pm = re.search(r'\(?\s*стр\.?\s*(\d+)', head)
        page = int(pm.group(1)) - 1 if pm else None       # 1-based показ → 0-based page_index (best-effort)
        title = re.sub(r'\s*\(.*$', '', head).strip()      # убрать "(стр A–B)"
        if title and body:
            chapters.append((title, page, body))
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
        page_index UNINDEXED, kind UNINDEXED, tokenize='unicode61');
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
            cx.execute("INSERT INTO ontology_fts(chapter_title,summary,source_id,source_slug,source_title,page_index,kind) "
                       "VALUES('',?,?,?,?,NULL,'book')", (book, sid, slug, stitle))
            nb += 1
        # курированные toc для best-effort привязки summary
        cur = cx.execute("SELECT id,title FROM toc WHERE source_id=? AND origin='curated'", (sid,)).fetchall()
        curmap = {norm(t): tid for tid, t in cur}
        for title, page, summ in chapters:
            cx.execute("INSERT INTO ontology_fts(chapter_title,summary,source_id,source_slug,source_title,page_index,kind) "
                       "VALUES(?,?,?,?,?,?,'chapter')", (title, summ, sid, slug, stitle, page))
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
