"""
VetAI ingest (M1): строит index.db (SQLite + FTS5) из S3-бакета.
Идемпотентно: пересоздаёт БД с нуля. Ключи S3 — из переменных окружения.

  AK / SK  — access/secret key (обязательно)
  S3_ENDPOINT, S3_BUCKET, S3_REGION — опционально (есть дефолты)
  GALEN_LIMIT — ограничить число препаратов Galen (для быстрого прогона), 0 = все

Запуск:  AK=... SK=... python ingest/ingest.py
"""
import os, io, re, json, sqlite3, time
from concurrent.futures import ThreadPoolExecutor
import boto3, yaml
from botocore.config import Config

ENDPOINT = os.environ.get("S3_ENDPOINT", "https://s3.twcstorage.ru")
BUCKET   = os.environ.get("S3_BUCKET",   "lz810806-ai-data")
REGION   = os.environ.get("S3_REGION",   "ru-1")
GALEN_LIMIT = int(os.environ.get("GALEN_LIMIT", "0"))

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "..", "data", "index.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def s3client():
    return boto3.client("s3", endpoint_url=ENDPOINT,
        aws_access_key_id=os.environ["AK"], aws_secret_access_key=os.environ["SK"],
        region_name=REGION, config=Config(signature_version="s3v4",
        retries={"max_attempts": 5}, max_pool_connections=32))

s3 = s3client()

# вид животного / тип по имени датасета
DATASET_META = {
 "anemia_and_drugs_used_in_treatment":            ("multi", "book"),
 "antimicrobial_prescribing_guidelines_for_pigs": ("swine", "guide"),
 "antimicrobial_therapy_handbook":                ("multi", "book"),
 "antimicrobial_usage_in_pig_production":         ("swine", "book"),
 "atrophic_rhinitis_of_pigs":                     ("swine", "book"),
 "avian_biocheck":                                ("avian", "guide"),
 "avian_pathology":                               ("avian", "book"),
 "birds_biology_and_pathology":                   ("avian", "book"),
 "diseases_of_poultry":                           ("avian", "book"),
 "fattening_pigs_practical_guide":                ("swine", "guide"),
 "guide_to_working_on_growing_and_fattening":     ("swine", "guide"),
 "modern_pig_farming":                            ("swine", "book"),
 "pathological_diagnostics_diseases_pigs":        ("swine", "book"),
 "pcr_test":                                      ("multi", "reference"),
 "peisak_disease_of_pigs":                        ("swine", "book"),
 "pig_breeders_workshop":                         ("swine", "book"),
 "pigs_pharmacokinetics_dynamics_antibacterial_drugs": ("swine", "book"),
 "poultry_health_guide":                          ("avian", "guide"),
 "practical_guide_to_broiler_health_management":  ("avian", "guide"),
 "sows_practical_guide":                          ("swine", "guide"),
 "streptococcosis_dissertation":                  ("swine", "dissertation"),
 "vic_articles_avian":                            ("avian", "articles"),
 "vic_articles_swine":                            ("swine", "articles"),
}
TITLE_OVERRIDE = {
 "diseases_of_poultry": "Diseases of Poultry (14th Edition)",
 "vic_articles_avian":  "Статьи ВИК: птица",
 "vic_articles_swine":  "Статьи ВИК: свиньи",
 "drugs": "Справочник препаратов",
}

def get_bytes(key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()

def get_text(key):
    return get_bytes(key).decode("utf-8", "replace")

def list_all(prefix, delimiter=None):
    pag = s3.get_paginator("list_objects_v2")
    kw = dict(Bucket=BUCKET, Prefix=prefix)
    if delimiter: kw["Delimiter"] = delimiter
    objs, prefixes = [], []
    for page in pag.paginate(**kw):
        objs += [(o["Key"], o["Size"]) for o in page.get("Contents", [])]
        prefixes += [c["Prefix"] for c in page.get("CommonPrefixes", [])]
    return objs, prefixes

# ---------- schema ----------
def build_schema(cx):
    cx.executescript("""
    DROP TABLE IF EXISTS sources;
    DROP TABLE IF EXISTS pages;
    DROP TABLE IF EXISTS toc;
    DROP TABLE IF EXISTS preparations;
    DROP TABLE IF EXISTS diseases;
    DROP TABLE IF EXISTS pages_fts;
    DROP TABLE IF EXISTS preparations_fts;
    DROP TABLE IF EXISTS diseases_fts;

    CREATE TABLE sources(
      id INTEGER PRIMARY KEY, slug TEXT, title TEXT, species TEXT, kind TEXT,
      language TEXT DEFAULT 'ru', num_pages INTEGER, pdf_s3key TEXT,
      source_url TEXT, description TEXT);
    CREATE TABLE pages(
      id INTEGER PRIMARY KEY, source_id INTEGER, page_index INTEGER, markdown TEXT);
    CREATE TABLE toc(
      id INTEGER PRIMARY KEY, source_id INTEGER, title TEXT, level INTEGER, page_index INTEGER);
    CREATE TABLE preparations(
      id INTEGER PRIMARY KEY, origin TEXT, trade_name TEXT, generic_name TEXT,
      drug_class TEXT, dosage_form TEXT, route TEXT, target_animals TEXT,
      manufacturer TEXT, reg_number TEXT, instruction_md TEXT);
    CREATE TABLE diseases(
      id INTEGER PRIMARY KEY, slug TEXT, species TEXT, name TEXT, data_json TEXT);

    CREATE VIRTUAL TABLE pages_fts USING fts5(
      markdown, source_id UNINDEXED, source_title UNINDEXED, page_index UNINDEXED,
      tokenize='unicode61');
    CREATE VIRTUAL TABLE preparations_fts USING fts5(
      trade_name, generic_name, drug_class, target_animals, instruction_md,
      prep_id UNINDEXED, tokenize='unicode61');
    CREATE VIRTUAL TABLE diseases_fts USING fts5(
      name, body, disease_id UNINDEXED, tokenize='unicode61');
    """)

HEADING = re.compile(r'^(#{1,2})\s+(.+?)\s*#*$', re.M)
def extract_toc(markdown, page_index, out):
    for m in HEADING.finditer(markdown):
        title = m.group(2).strip()
        if 2 <= len(title) <= 120:
            out.append((title, len(m.group(1)), page_index))

# ---------- books & articles ----------
def ingest_sources(cx):
    _, ds_prefixes = list_all("VetAI/knowledge/data/", delimiter="/")
    src_id = 0; page_id = 0; toc_id = 0
    for pref in sorted(ds_prefixes):
        slug = pref.rstrip("/").split("/")[-1]
        if slug == "drugs":            # это таблица препаратов, не читаемый источник
            continue
        species, kind = DATASET_META.get(slug, ("multi", "book"))
        objs, _ = list_all(pref)
        jsons = sorted(k for k,_ in objs if k.endswith(".json"))
        mds   = sorted(k for k,_ in objs if k.endswith(".md"))
        pdfs  = [k for k,_ in objs if k.endswith(".pdf")]
        pdf_key = pdfs[0] if pdfs else None
        title = TITLE_OVERRIDE.get(slug, slug.replace("_", " ").capitalize())

        pages = []   # (markdown,)
        toc_rows = []
        if kind == "articles" or (not jsons and mds):
            # каждая статья/файл .md = страница
            for k in mds:
                md = get_text(k)
                idx = len(pages)
                pages.append(md)
                # заголовок статьи = первый # или имя файла
                m = HEADING.search(md)
                art_title = m.group(2).strip() if m else k.split("/")[-1][:80]
                toc_rows.append((art_title, 1, idx))
        else:
            # книга: pages[] из одного/нескольких json (с непрерывной нумерацией)
            for jk in jsons:
                try:
                    data = json.loads(get_bytes(jk))
                except Exception:
                    continue
                for p in data.get("pages", []):
                    md = p.get("markdown", "") or ""
                    idx = len(pages)
                    pages.append(md)
                    extract_toc(md, idx, toc_rows)

        if not pages:
            continue
        src_id += 1
        cx.execute("INSERT INTO sources(id,slug,title,species,kind,num_pages,pdf_s3key) "
                   "VALUES(?,?,?,?,?,?,?)", (src_id, slug, title, species, kind, len(pages), pdf_key))
        cx.executemany("INSERT INTO pages(source_id,page_index,markdown) VALUES(?,?,?)",
                       [(src_id, i, md) for i, md in enumerate(pages)])
        cx.executemany("INSERT INTO pages_fts(markdown,source_id,source_title,page_index) VALUES(?,?,?,?)",
                       [(md, src_id, title, i) for i, md in enumerate(pages)])
        cx.executemany("INSERT INTO toc(source_id,title,level,page_index) VALUES(?,?,?,?)",
                       [(src_id, t, lv, pi) for (t, lv, pi) in toc_rows])
        print(f"  [source] {slug:48s} pages={len(pages):5d} toc={len(toc_rows):4d} pdf={'y' if pdf_key else '-'}")
    return src_id

# ---------- pharma: drugs_table.md (Вик, чистые поля) ----------
def ingest_drugs_table(cx):
    key = "VetAI/knowledge/data/drugs/drugs_table.md"
    try:
        md = get_text(key)
    except Exception as e:
        print("  drugs_table.md not found:", e); return 0
    rows = [ln for ln in md.splitlines() if ln.strip().startswith("|")]
    n = 0
    for ln in rows:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 8: continue
        if cells[0] in ("ID", "--") or set(cells[0]) <= set("-: "): continue
        _id, trade, generic, dclass, form, route, animals, manuf = cells[:8]
        if not _id.isdigit(): continue
        cx.execute("""INSERT INTO preparations(origin,trade_name,generic_name,drug_class,
                      dosage_form,route,target_animals,manufacturer) VALUES('drugs',?,?,?,?,?,?,?)""",
                   (trade, generic, dclass, form, route, animals, manuf))
        pid = cx.execute("SELECT last_insert_rowid()").fetchone()[0]
        cx.execute("INSERT INTO preparations_fts(trade_name,generic_name,drug_class,target_animals,instruction_md,prep_id) "
                   "VALUES(?,?,?,?,?,?)", (trade, generic, dclass, animals, "", pid))
        n += 1
    print(f"  [pharma:drugs] rows={n}")
    return n

# ---------- pharma: galen (гос.реестр, ~2360) ----------
TRADE_RE = re.compile(r'Торгов\w*\s+наименовани\w*[:\s]+([^\.\n]+)', re.I)
def _fetch_galen(prefix):
    try:
        sec = json.loads(get_bytes(prefix + "sections.json"))
    except Exception:
        return None
    meta = {}
    try:
        meta = json.loads(get_bytes(prefix + "metadata.json"))
    except Exception:
        pass
    gi = sec.get("general_info", "") or ""
    m = TRADE_RE.search(gi)
    trade = (m.group(1).strip() if m else "").strip() or (meta.get("clientView","")[:60] or prefix.rstrip("/").split("/")[-1])
    instr = "\n\n".join(f"## {k}\n{v}" for k, v in sec.items()
                        if not k.startswith("_") and isinstance(v, str) and v.strip())
    return dict(trade=trade, generic=sec.get("composition","")[:200],
                manuf=meta.get("clientView",""), reg=meta.get("regNumber",""),
                instr=instr)

def ingest_galen(cx):
    _, folders = list_all("VetAI/knowledge/galen/preparations/", delimiter="/")
    folders = [f for f in folders if re.search(r'/\d+/$', f)]
    if GALEN_LIMIT: folders = folders[:GALEN_LIMIT]
    print(f"  [pharma:galen] folders to fetch: {len(folders)} ...")
    t0 = time.time(); n = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        for rec in ex.map(_fetch_galen, folders):
            if not rec: continue
            cx.execute("""INSERT INTO preparations(origin,trade_name,generic_name,
                          manufacturer,reg_number,instruction_md)
                          VALUES('galen',?,?,?,?,?)""",
                       (rec["trade"], rec["generic"], rec["manuf"], rec["reg"], rec["instr"]))
            pid = cx.execute("SELECT last_insert_rowid()").fetchone()[0]
            cx.execute("INSERT INTO preparations_fts(trade_name,generic_name,drug_class,target_animals,instruction_md,prep_id) "
                       "VALUES(?,?,?,?,?,?)", (rec["trade"], rec["generic"], "", "", rec["instr"], pid))
            n += 1
            if n % 500 == 0: print(f"     ...{n} ({time.time()-t0:.0f}s)")
    print(f"  [pharma:galen] rows={n} in {time.time()-t0:.0f}s")
    return n

# ---------- diseases (yml) ----------
def ingest_diseases(cx):
    n = 0
    for species, pref in [("avian", "VetAI/knowledge/diagnostics/avian_diseases/list/"),
                          ("swine", "VetAI/knowledge/diagnostics/swine_diseases/list/")]:
        objs, _ = list_all(pref)
        for k, _sz in objs:
            if not k.endswith(".yml"): continue
            try:
                doc = yaml.safe_load(get_text(k))
            except Exception:
                continue
            if not isinstance(doc, dict): continue
            slug = k.split("/")[-1].replace(".yml", "")
            top = doc.get(slug) or next(iter(doc.values()))
            name = (top or {}).get("disease_name", slug) if isinstance(top, dict) else slug
            data_json = json.dumps(top, ensure_ascii=False)
            cx.execute("INSERT INTO diseases(slug,species,name,data_json) VALUES(?,?,?,?)",
                       (slug, species, name, data_json))
            did = cx.execute("SELECT last_insert_rowid()").fetchone()[0]
            cx.execute("INSERT INTO diseases_fts(name,body,disease_id) VALUES(?,?,?)",
                       (name, data_json, did))
            n += 1
    print(f"  [diseases] rows={n}")
    return n

def main():
    t0 = time.time()
    if os.path.exists(DB_PATH): os.remove(DB_PATH)
    cx = sqlite3.connect(DB_PATH)
    build_schema(cx)
    print("== sources (books/articles) ==");   ingest_sources(cx);        cx.commit()
    print("== pharma: drugs table ==");        ingest_drugs_table(cx);    cx.commit()
    print("== pharma: galen registry ==");     ingest_galen(cx);          cx.commit()
    print("== diseases ==");                   ingest_diseases(cx);       cx.commit()
    # сводка
    print("\n=== SUMMARY (index.db) ===")
    for t in ["sources", "pages", "toc", "preparations", "diseases"]:
        c = cx.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:14s} {c}")
    print("  preparations by origin:",
          dict(cx.execute("SELECT origin,COUNT(*) FROM preparations GROUP BY origin").fetchall()))
    cx.close()
    size = os.path.getsize(DB_PATH)/1024/1024
    print(f"\nDB: {DB_PATH}  ({size:.1f} MB)  in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
