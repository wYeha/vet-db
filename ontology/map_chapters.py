"""Сопоставление курированных глав (toc origin='curated', без page_index) с фактическими
markdown-заголовками уровня 1 (origin='markdown' level=1, у них есть page_index).

Почему так: курированные оглавления чистые, но без страниц. Реальное начало главы —
это markdown `#`-заголовок в теле книги. Матчим по названию (exact → fuzzy), с
МОНОТОННОСТЬЮ (старт каждой главы строго позже предыдущей) — это отсекает ложные
ранние совпадения (печатное оглавление на первых страницах). Служебные разделы
(оглавление, список таблиц/литературы, резюме) помечаются skip.

Запуск:
  python ontology/map_chapters.py            # проверка на пилотных книгах
  python ontology/map_chapters.py --all      # записать ontology/chapters/<slug>.json на все книги
"""
import sqlite3, re, json, os, sys

DB = os.path.join(os.path.dirname(__file__), "..", "data", "index.db")
OUT = os.path.join(os.path.dirname(__file__), "chapters")

SERVICE_RE = re.compile(
    r'оглавлен|содержан|список\s+табл|список\s+литерат|литература|references|резюме|summary|contents|указател',
    re.I)

def norm(s):
    s=(s or "").lower()
    s=re.sub(r'[^0-9a-zа-яё ]',' ',s)
    return re.sub(r'\s+',' ',s).strip()

def toks(s): return set(w for w in norm(s).split() if len(w)>=3)

def jaccard(a,b):
    A,B=toks(a),toks(b)
    if not A or not B: return 0.0
    return len(A&B)/len(A|B)

def map_book(cx, sid, npg):
    cur=cx.execute("SELECT title,level FROM toc WHERE source_id=? AND origin='curated' ORDER BY id",(sid,)).fetchall()
    md1=cx.execute("SELECT title,page_index FROM toc WHERE source_id=? AND origin='markdown' AND level=1 ORDER BY page_index,id",(sid,)).fetchall()
    mapped=[]; prev=-1
    for title,level in cur:
        if SERVICE_RE.search(title or ""):
            mapped.append([title,level,None,"skip-service"]); continue
        nt=norm(title)
        best=None; best_score=0.0; best_how=""
        for mt,pi in md1:
            if pi<=prev: continue           # монотонность
            mn=norm(mt)
            if nt==mn:                       # точное совпадение — сразу берём
                best=pi; best_score=1.0; best_how="exact"; break
            sc=jaccard(title,mt)
            # поглощение (одно название содержит другое) считаем сильным матчем
            if nt and (nt in mn or mn in nt) and min(len(nt),len(mn))>=6:
                sc=max(sc,0.85)
            if sc>best_score:
                best,best_score,best_how=pi,sc,f"fuzzy{sc:.2f}"
        if best is not None and best_score>=0.34:
            mapped.append([title,level,best,best_how]); prev=best
        else:
            mapped.append([title,level,None,"unmatched"])
    # диапазоны: конец = старт следующей сопоставленной главы
    starts=[m[2] for m in mapped]
    for i,m in enumerate(mapped):
        if m[2] is None: m.append(None); continue
        end=npg
        for j in range(i+1,len(mapped)):
            if mapped[j][2] is not None and mapped[j][2]>m[2]:
                end=mapped[j][2]; break
        m.append(end)
    return mapped

def run(cx, slug, verbose=True):
    row=cx.execute("SELECT id,num_pages FROM sources WHERE slug=?",(slug,)).fetchone()
    sid,npg=row
    mapped=map_book(cx,sid,npg)
    ok=sum(1 for m in mapped if m[2] is not None)
    skip=sum(1 for m in mapped if m[3]=="skip-service")
    if verbose:
        print(f"\n== {slug} (id={sid},{npg}стр) — глав {len(mapped)}, сопоставлено {ok}, служебных {skip} ==")
        for title,level,start,how,end in mapped:
            rng = f"стр {start}-{end-1}" if start is not None else f"— ({how})"
            print(f"  {rng:16} [{how:10}] {title[:56]}")
    # ВСЕ курированные главы (с диапазоном где сматчилось, иначе start=null — субагент выровняет)
    chapters=[{"title":t,"level":l,"start":s,"end":e,"method":h}
              for t,l,s,h,e in mapped if h!="skip-service"]
    md1=[{"title":mt,"page":pi} for mt,pi in
         cx.execute("SELECT title,page_index FROM toc WHERE source_id=? AND origin='markdown' AND level=1 ORDER BY page_index,id",(sid,)).fetchall()]
    return sid,npg,chapters,md1

if __name__=="__main__":
    cx=sqlite3.connect(DB)
    if "--all" in sys.argv:
        os.makedirs(OUT,exist_ok=True)
        slugs=[r[0] for r in cx.execute("SELECT slug FROM sources ORDER BY id")]
        summary=[]
        for slug in slugs:
            sid,npg,chapters,md1=run(cx,slug,verbose=False)
            json.dump({"slug":slug,"source_id":sid,"num_pages":npg,
                       "chapters":chapters,"markdown_headings":md1},
                      open(os.path.join(OUT,f"{slug}.json"),"w",encoding="utf-8"),ensure_ascii=False,indent=1)
            summary.append((slug,len(chapters)))
            print(f"  {slug:48} глав с диапазоном: {len(chapters)}")
        print(f"\nзаписано {len(slugs)} файлов в ontology/chapters/; всего глав: {sum(c for _,c in summary)}")
    else:
        for slug in ["atrophic_rhinitis_of_pigs","peisak_disease_of_pigs"]:
            run(cx,slug)
