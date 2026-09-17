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
      id INTEGER PRIMARY KEY, slug TEXT, title TEXT,
      language TEXT DEFAULT 'ru', num_pages INTEGER, pdf_s3key TEXT,
      source_url TEXT, description TEXT);
    CREATE TABLE pages(
      id INTEGER PRIMARY KEY, source_id INTEGER, page_index INTEGER, markdown TEXT);
    CREATE TABLE toc(
      id INTEGER PRIMARY KEY, source_id INTEGER, title TEXT, level INTEGER,
      page_index INTEGER, origin TEXT DEFAULT 'markdown');
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
        objs, _ = list_all(pref)
        jsons = sorted(k for k,_ in objs if k.endswith(".json"))
        mds   = sorted(k for k,_ in objs if k.endswith(".md"))
        pdfs  = [k for k,_ in objs if k.endswith(".pdf")]
        pdf_key = pdfs[0] if pdfs else None
        # заголовок — механически из имени папки (никакой ручной классификации)
        title = slug.replace("_", " ")

        pages = []   # markdown по строке на страницу
        toc_rows = []
        # книга: берём json, где есть ключ "pages" (структурный признак, не метка)
        for jk in jsons:
            try:
                data = json.loads(get_bytes(jk))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("pages"):
                for p in data["pages"]:
                    md = p.get("markdown", "") or ""
                    pages.append(md)
                    extract_toc(md, len(pages) - 1, toc_rows)
        # нет постраничного json, но есть .md → каждый файл = страница (статьи/справка)
        if not pages and mds:
            for k in mds:
                md = get_text(k)
                pages.append(md)
                m = HEADING.search(md)
                art_title = m.group(2).strip() if m else k.split("/")[-1][:80]
                toc_rows.append((art_title, 1, len(pages) - 1))

        if not pages:
            continue
        src_id += 1
        cx.execute("INSERT INTO sources(id,slug,title,num_pages,pdf_s3key) "
                   "VALUES(?,?,?,?,?)", (src_id, slug, title, len(pages), pdf_key))
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
    product = meta.get("product") or {}
    gi = sec.get("general_info", "") or ""
    # Торговое название: сначала структурированное product.name из реестра, затем
    # регэксп по OCR-тексту general_info, затем номер папки. НИКОГДА не производителя
    # (clientView) — именно фолбэк на него давал баг «название = компания».
    m = TRADE_RE.search(gi)
    trade = ((product.get("name") or "").strip()
             or (m.group(1).strip() if m else "")
             or prefix.rstrip("/").split("/")[-1])
    generic = (product.get("chemicalName") or "").strip() or sec.get("composition", "")[:200]
    dclass = (product.get("drugGroup") or "").strip()
    instr = "\n\n".join(f"## {k}\n{v}" for k, v in sec.items()
                        if not k.startswith("_") and isinstance(v, str) and v.strip())
    return dict(trade=trade, generic=generic, dclass=dclass,
                manuf=meta.get("clientView", ""), reg=meta.get("regNumber", ""),
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
            cx.execute("""INSERT INTO preparations(origin,trade_name,generic_name,drug_class,
                          manufacturer,reg_number,instruction_md)
                          VALUES('galen',?,?,?,?,?,?)""",
                       (rec["trade"], rec["generic"], rec["dclass"], rec["manuf"], rec["reg"], rec["instr"]))
            pid = cx.execute("SELECT last_insert_rowid()").fetchone()[0]
            cx.execute("INSERT INTO preparations_fts(trade_name,generic_name,drug_class,target_animals,instruction_md,prep_id) "
                       "VALUES(?,?,?,?,?,?)", (rec["trade"], rec["generic"], rec["dclass"], "", rec["instr"], pid))
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

# ---------- ontology: курированные оглавления из source_document.contents ----------
# Дампы: VetAI/database_data/dumps/<hash>/source_document*.sql
# Таблица source_document(id, name, language, contents, created_at), где contents =
# готовое (курированное) оглавление книги — по одной главе на строку. LLM не используется.

# Стабильное соответствие source_document.id -> slug наших книг (курированная карта,
# без модели). Имена в дампе — человекочитаемые (ru/en), slug'и — транслитерация,
# поэтому прямое сравнение строк их не свяжет; id из дампа стабильны.
SDOC_SLUG = {
    1:  "peisak_disease_of_pigs",                       # Болезни свиней
    2:  "poultry_health_guide",                         # Poultry Health...
    3:  "birds_biology_and_pathology",                  # Биология и патология с/х птицы
    4:  "avian_pathology",                              # Avian Pathology 2020
    5:  "pigs_pharmacokinetics_dynamics_antibacterial_drugs",  # PK/PD antimicrobials pigs
    6:  "antimicrobial_therapy_handbook",               # Antimicrobial Therapy in Vet. Medicine
    7:  "practical_guide_to_broiler_health_management",
    8:  "antimicrobial_prescribing_guidelines_for_pigs",
    9:  "antimicrobial_usage_in_pig_production",
    13: "diseases_of_poultry",
    14: "atrophic_rhinitis_of_pigs",                    # Атрофический ринит свиней
    15: "anemia_and_drugs_used_in_treatment",           # Анемия и препараты...
    16: "streptococcosis_dissertation",                 # Патоморфология стрептококкоза свиней
    17: "pathological_diagnostics_diseases_pigs",       # Патологоанатомическая диагностика
    18: "sows_practical_guide",                         # Свиноматки...
    19: "fattening_pigs_practical_guide",               # Откорм свиней...
    20: "pig_breeders_workshop",                        # Практикум свиновода
    21: "modern_pig_farming",                           # Современное свиноводство
}

# INSERT INTO source_document (id, name, language, contents, created_at) VALUES (...)
SDOC_RE = re.compile(
    r"INSERT INTO source_document \([^)]*\) VALUES\s*\(\s*(\d+)\s*,\s*"
    r"'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'\s*,\s*"
    r"'((?:[^']|'')*)'\s*\)", re.S)
CHAP_NUM_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)[.)]?\s+')

def _norm_name(s):
    return re.sub(r'[^0-9a-zа-яё]+', '', (s or '').lower())

def _parse_contents(contents):
    """contents -> [(title, level)]; level по числовой нумерации, page_index всегда NULL."""
    rows = []
    for raw in contents.replace("\r\n", "\n").split("\n"):
        title = raw.strip()
        if not title:
            continue
        title = title[:200]
        m = CHAP_NUM_RE.match(title)
        level = len(m.group(1).split(".")) if m else 1
        if level > 4:
            level = 4
        rows.append((title, level))
    return rows

def ingest_ontology(cx):
    # карты slug/имя -> source_id из уже загруженных sources
    slug2id, norm2id = {}, {}
    for sid, slug, title in cx.execute("SELECT id, slug, title FROM sources").fetchall():
        if slug:
            slug2id[slug] = sid
        if title:
            norm2id[_norm_name(title)] = sid
        if slug:
            norm2id.setdefault(_norm_name(slug.replace("_", " ")), sid)

    objs, _ = list_all("VetAI/database_data/dumps/")
    files = sorted(k for k, _sz in objs if "source_document" in k and k.endswith(".sql"))
    print(f"  source_document files: {len(files)}")

    curated_rows = 0
    matched, unmatched = [], []
    for k in files:
        try:
            txt = get_text(k)
        except Exception as e:
            print("  ! read fail", k, e); continue
        for m in SDOC_RE.finditer(txt):
            did = int(m.group(1))
            name = m.group(2).replace("''", "'").lstrip("﻿").strip()
            contents = m.group(4).replace("''", "'")
            # матчинг: сначала курированная карта id->slug, затем нормализованное имя
            sid = None
            slug = SDOC_SLUG.get(did)
            if slug and slug in slug2id:
                sid = slug2id[slug]
            if sid is None:
                sid = norm2id.get(_norm_name(name))
            if sid is None:
                unmatched.append((did, name)); continue
            chapters = _parse_contents(contents)
            if not chapters:
                unmatched.append((did, name)); continue
            cx.executemany(
                "INSERT INTO toc(source_id,title,level,page_index,origin) "
                "VALUES(?,?,?,NULL,'curated')",
                [(sid, t, lv) for (t, lv) in chapters])
            curated_rows += len(chapters)
            matched.append((did, name, len(chapters)))

    print(f"  [ontology] matched source_document -> sources: {len(matched)}, "
          f"unmatched: {len(unmatched)}, curated toc rows: {curated_rows}")
    for did, name, n in sorted(matched):
        print(f"     +curated sdoc#{did:<4d} chapters={n:<3d} {name[:60]}")
    if unmatched:
        print(f"     unmatched (первые 10 из {len(unmatched)}): "
              + "; ".join(f"#{d} {nm[:40]}" for d, nm in unmatched[:10]))
    return curated_rows

def main():
    t0 = time.time()
    if os.path.exists(DB_PATH): os.remove(DB_PATH)
    cx = sqlite3.connect(DB_PATH)
    build_schema(cx)
    print("== sources (books/articles) ==");   ingest_sources(cx);        cx.commit()
    print("== ontology: curated toc ==");       ingest_ontology(cx);       cx.commit()
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
    print("  toc by origin:",
          dict(cx.execute("SELECT origin,COUNT(*) FROM toc GROUP BY origin").fetchall()))
    cx.close()
    size = os.path.getsize(DB_PATH)/1024/1024
    print(f"\nDB: {DB_PATH}  ({size:.1f} MB)  in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
