# VetAI Knowledge Base — Спецификация (MVP)

> Документ-спека для сборки с нуля. Пишется под «vibe-coding»: его целиком
> отдают кодовому агенту (Codex / Cursor / Bolt / Replit), агент реализует.
> Стек намеренно оставлен свободным — жёстко зафиксированы **модель данных и
> контракт API**, а не язык/фреймворк. Можно собрать несколькими инструментами
> и сравнить результат.

---

## 1. Что и зачем строим

Веб-база знаний по ветеринарии продуктивных животных (свиньи, птица, КРС) с
**двумя потребителями**:

1. **Человек** — заходит в веб-интерфейс, листает книги/статьи как читалку,
   ищет внутри книги (как `Ctrl+F` в PDF), открывает карточки препаратов.
2. **Агент** (⏳ 2-я итерация) — тот же контент доступен как «навык» (skill) с
   HTTP-эндпоинтами, чтобы внешний агент (opencode у Миши и т.п.) подключался
   «одним движением». В 1-й итерации **не делаем** — только веб для человека.

### Принципы
- **S3 — источник правды.** Данные уже распознаны и лежат в S3. Мы их **не
  мигрируем и не пересчитываем**, а строим поверх тонкий индекс.
- **Индекс = один файл SQLite** (FTS5 для полнотекста + пара таблиц для
  фильтров). Никакого Postgres/векторной БД — корпус крошечный (~57 МБ текста).
- **Переиспользуем готовое.** Вектора, оглавления, номера страниц, ключевые
  слова уже посчитаны и лежат в SQL-дампах — берём оттуда, не распознаём заново.
- **Поиск — «как Яндекс».** Эндпоинт `search` только находит и возвращает хиты
  (без LLM-выводов). Таблицу/ответ из хитов собирает уже агент.
- **Легко и быстро.** Одно небольшое приложение, один деплой. Не оверинжинирим.

### Вне scope 1-й итерации (осознанно отложено)
- **Skill/эндпоинты для агента — 2-я итерация.** Сейчас строим только веб-читалку
  для человека. API §4 проектируем сразу как чистый REST, чтобы во 2-й итерации
  навык для агента лёг сверху без переделок.
- Превью-картинки препаратов и PDF-инструкции препаратов — **их нет в бакете**
  (см. §9). MVP живёт без них: у препарата есть текст инструкции.
- Векторный/семантический поиск — **опционально, фаза 2** (данные готовы, но
  стартуем на полнотексте + оглавлениях; см. §7).
- Встроенный в веб LLM-ассистент, который сам пишет ответы, — фаза 2.
- Доступ к внешнему бакету `cx96074-ai-data` — пока не трогаем.

---

## 2. Источники данных в S3

Подключение (ключи — через переменные окружения, **не хардкодить**):

```
S3_ENDPOINT = https://s3.twcstorage.ru
S3_BUCKET   = lz810806-ai-data
S3_REGION   = ru-1
S3_ACCESS_KEY / S3_SECRET_KEY  — из env
```

Раскладка внутри бакета (проверено разведкой):

```
VetAI/
├── knowledge/
│   ├── data/<dataset>/            # 24 датасета = книги/статьи
│   │   ├── <name>.json            #   постранично распознанный markdown (см. §2.1)
│   │   ├── <name>.pdf             #   оригинал (есть у 19 из 24)
│   │   └── parsed_images/ | images/   # иллюстрации (jpeg/png)
│   ├── diagnostics/
│   │   ├── avian_diseases/list/*.yml   # 11 болезней птицы (структурировано)
│   │   └── swine_diseases/list/*.yml   # 11 болезней свиней
│   └── galen/
│       └── preparations/<N>/      # ~2360 препаратов гос.реестра
│           ├── instruction.md     #   текст инструкции
│           ├── sections.json      #   поля: indications/dosage/composition/...
│           └── metadata.json      #   регистрационные поля (производитель, рег.№)
└── database_data/
    ├── dumps/<hash>/*.sql         # готовые таблицы (см. §2.2) — источник для обогащения
    └── snapshots/vetai_seed_*.dump # полный снапшот PostgreSQL (pg_dump custom)
```

### 2.1. Формат книги (`data/<dataset>/<name>.json`)
```json
{
  "pages": [
    { "index": 0, "markdown": "# Заголовок...\n## ...", "images": [], "dimensions": {...} },
    ...
  ],
  "model": "utils/pdf-ocr-1.0",
  "usage_info": { "pages_processed": 700, "doc_size_bytes": 41314080 },
  "text": "весь текст одной строкой"
}
```
Ключевое: **каждая страница уже готовый markdown с заголовками `#`/`##`.** Из
этого извлекается и оглавление (по заголовкам), и постраничный поиск с цитатой
«страница N». Некоторые датасеты (статьи Вика) вместо одного json — набор `.md`.

### 2.2. Схемы таблиц в дампах (для обогащения индекса)
```sql
-- Препараты (≈166 строк, «каталог Вика») — чистые поля + вектор
drugs(id, trade_name, generic_name, drug_class, dosage_form, route,
      target_animals, manufacturer, instruction /*markdown*/, created_at, embedding)

-- Чанки для поиска: уже с векторами, страницами, главами, ключевыми словами
knowledge_base_chunks(id, content, content_type, content_name, embedding,
      page_number, chunk_number, chapter_title, keywords, created_at, source_document_id)

-- Документы: contents = ГОТОВОЕ ОГЛАВЛЕНИЕ книги
source_document(id, name, language, contents, created_at)

-- Картинки книг/статей (бинарь в hex), привязаны к чанку/документу (НЕ к препаратам)
images(id, chunk_id, image_data, created_at, source_document)
```
Вектора пересчитаны на qwen3: `*_qwen3.sql`. Дампы — обычные `INSERT INTO ...`,
парсятся построчно без поднятия Postgres; либо восстановить `*.dump` в временный
Postgres и выгрузить `SELECT`-ом.

---

## 3. Сущности и модель данных (индекс SQLite)

Один файл `index.db`. Пересобирается из S3 в любой момент (см. §5).

```sql
-- ИСТОЧНИКИ (книга / набор статей / руководство / диссертация)
CREATE TABLE sources (
  id            INTEGER PRIMARY KEY,
  slug          TEXT UNIQUE,          -- напр. 'diseases_of_poultry'
  title         TEXT,                 -- человекочитаемое название
  species       TEXT,                 -- 'swine' | 'avian' | 'cattle' | 'multi'
  kind          TEXT,                 -- 'book' | 'articles' | 'guide' | 'dissertation'
  language       TEXT DEFAULT 'ru',
  num_pages     INTEGER,
  pdf_s3key     TEXT,                 -- ключ оригинала в S3 (может быть NULL)
  source_url    TEXT,                 -- для статей Вика (может быть NULL)
  description   TEXT
);

-- СТРАНИЦЫ (единица чтения и цитирования)
CREATE TABLE pages (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER REFERENCES sources(id),
  page_index  INTEGER,               -- 0-based, как в json.pages[].index
  markdown    TEXT
);
CREATE VIRTUAL TABLE pages_fts USING fts5(
  markdown, content='pages', content_rowid='id', tokenize='unicode61'
);

-- ОГЛАВЛЕНИЕ (онтология для человека и агента)
CREATE TABLE toc (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER REFERENCES sources(id),
  title       TEXT,
  level       INTEGER,               -- 1 = глава, 2 = раздел...
  page_index  INTEGER
);

-- ПРЕПАРАТЫ (фарма-записи; MVP-структура из таблицы drugs)
CREATE TABLE preparations (
  id             INTEGER PRIMARY KEY,
  origin         TEXT,               -- 'drugs'(Вик) | 'galen'(реестр)
  trade_name     TEXT,
  generic_name   TEXT,
  drug_class     TEXT,               -- для фильтра «антибиотики ...»
  dosage_form    TEXT,
  route          TEXT,
  target_animals TEXT,               -- для фильтра «... для КРС»
  manufacturer   TEXT,
  reg_number     TEXT,
  instruction_md TEXT                -- текст инструкции (markdown)
);
CREATE VIRTUAL TABLE preparations_fts USING fts5(
  trade_name, generic_name, drug_class, target_animals, instruction_md,
  content='preparations', content_rowid='id', tokenize='unicode61'
);

-- БОЛЕЗНИ (диагностика; из yml)
CREATE TABLE diseases (
  id        INTEGER PRIMARY KEY,
  slug      TEXT,
  species   TEXT,                    -- 'avian' | 'swine'
  name      TEXT,
  data_json TEXT                     -- полная структура болезни (etiology и т.д.)
);
CREATE VIRTUAL TABLE diseases_fts USING fts5(
  name, data_json, content='diseases', content_rowid='id', tokenize='unicode61'
);

-- (ФАЗА 2) чанки с векторами для семантики — заполняются из дампов
-- CREATE TABLE chunks(id, source_id, page_index, chapter_title, content,
--                     keywords, embedding BLOB);
```

**Решение по препаратам в MVP:**
- Структурированные фильтры (`drug_class`, `target_animals`) → берём из таблицы
  `drugs` (~166, каталог Вика: там эти поля чистые). Это закрывает примеры
  «антибиотики на А» и «препараты для КРС».
- ~2360 препаратов Galen → грузим как `origin='galen'` с текстовым поиском по
  `instruction_md` (у них нет чистого `drug_class`, но полнотекст работает).

---

## 4. API (контракт — это и есть спека; стек свободен)

Все ответы — JSON. Поиск **не** формулирует ответы, только отдаёт хиты.

```
GET /api/sources
    ?species=swine|avian|cattle  &kind=book|articles  &q=<подстрока по названию>
    -> [ {id, slug, title, species, kind, num_pages, has_pdf, source_url} ]

GET /api/sources/{id}
    -> {id, title, species, kind, num_pages, pdf_url, source_url, description}

GET /api/sources/{id}/toc
    -> [ {title, level, page_index} ]              # оглавление/онтология

GET /api/sources/{id}/pages/{index}
    -> {source_id, page_index, markdown, num_pages} # одна страница книги

GET /api/sources/{id}/pdf
    -> 302 redirect на presigned S3 URL оригинала (если pdf_s3key есть)

GET /api/search
    ?q=<строка>
    &scope=all|books|pharma|diseases   (default all)
    &source_id=<id>                    (поиск ВНУТРИ книги — режим Ctrl+F)
    &mode=keyword|semantic|hybrid      (default keyword; semantic — фаза 2)
    &limit=20
    -> [ {
         type: 'page'|'preparation'|'disease',
         source_id, source_title, page_index,   # для type=page (цитата!)
         ref_id, title,                          # для preparation/disease
         snippet,                                # подсвеченный фрагмент
         score
       } ]

GET /api/preparations
    ?drug_class=<...>  &animal=<...>  &q=<...>  &origin=drugs|galen  &limit=50
    -> [ {id, trade_name, generic_name, drug_class, target_animals, manufacturer} ]

GET /api/preparations/{id}
    -> {все поля + instruction_md}

GET /api/diseases ?species=avian|swine
GET /api/diseases/{id}
```

### Skill для агента (⏳ 2-я итерация — не в этой сборке)
В репозитории — файл-навык (`skill/SKILL.md` + при желании тонкий CLI-обёртка),
описывающий эти эндпоинты в стиле навыков opencode/Cursor/Codex. Формулировки —
как у существующего `vetdb`: команды `sources` (список источников) и `search`
(поиск, гибрид/ключевые слова/семантика). Навык объясняет агенту: «ищи через
`/api/search`, режим hybrid; результат — список хитов с указанием источника и
страницы; выводы делай сам». **В 1-й итерации не реализуем**, но REST §4 уже
готов под это.

---

## 5. Ingest / build (наполнение индекса)

Отдельный скрипт `ingest/` (рекомендуется Python + boto3), собирает `index.db`
из S3. Идемпотентно, пересобираемо. Порядок:

1. **sources + pages** — пройти `VetAI/knowledge/data/*/`:
   - если есть `<name>.json` → `pages[]` → строки `pages`; `num_pages`; заголовок;
   - если датасет из `.md` (статьи Вика) → каждый `.md` = запись (страница/статья);
   - определить `species`/`kind` по имени датасета (таблица маппинга в конфиге);
   - `pdf_s3key` = ключ `.pdf`, если есть.
2. **toc** — извлечь заголовки `#`/`##` из markdown каждой страницы → дерево
   оглавления. (Фаза 2: сверить/дополнить из `source_document.contents` дампов.)
3. **preparations** —
   - `drugs`-таблица из дампа `.../drugs*.sql` → `origin='drugs'` (парсить INSERT);
   - `galen/preparations/<N>/` → `sections.json` + `metadata.json` → `origin='galen'`.
4. **diseases** — распарсить `diagnostics/**/*.yml` → строки `diseases`.
5. Построить FTS-индексы (`INSERT INTO *_fts`).
6. (Фаза 2) **chunks** — импорт `knowledge_base_chunks*_qwen3.sql` (content,
   embedding, page_number, chapter_title, keywords) для семантики.

Замечание: имена файлов местами в «битой» кириллице (cp1251→utf8 mojibake) —
нормализовать при чтении ключей.

---

## 6. Веб-интерфейс (для человека)

Минимум экранов, аккуратный рендер, мобилка не обязательна (внутренний инструмент).

1. **Главная / «Сводка по базе»** — список всех источников, сгруппированы по виду
   животного (свиньи / птица / КРС), фильтры (вид, тип), глобальная строка поиска.
2. **Читалка источника** — слева оглавление (`toc`), справа страница (рендер
   markdown), постраничная навигация (‹ N / M ›). Сверху — **поиск внутри книги**
   (как `Ctrl+F`): вводишь слово (бацилла/вирус/…) → список совпадений по
   страницам → клик → переход на страницу + подсветка. Кнопка «Оригинал (PDF)».
3. **Результаты глобального поиска** — хиты сгруппированы по типу (страницы книг /
   препараты / болезни), у страниц — «Источник → стр. N».
4. **Фарма** — список препаратов с фильтрами `drug_class` / `target_animals` /
   поиск; карточка препарата = рендер `instruction_md` + поля (производитель,
   рег.№, действующее вещество). Картинок препаратов пока нет — это ок.
5. **Диагностика** — список болезней (птица/свиньи), карточка = структура из yml.

---

## 7. Поиск: поведение

- **MVP = keyword (FTS5).** Полнотекст по `pages_fts` / `preparations_fts` /
  `diseases_fts`. Мгновенно, ноль инфраструктуры. Даёт постраничные цитаты.
- **Внутри книги** = тот же FTS с фильтром `source_id` (режим Ctrl+F).
- **semantic / hybrid = фаза 2.** Данные готовы (вектора qwen3 в дампах). Когда
  понадобится recall по смыслу — импортировать чанки в `chunks.embedding` (BLOB) и
  считать косинус в приложении или через `sqlite-vec`. Отдельная БД не нужна.
- Эндпоинт всегда возвращает только хиты; ответ/таблицу строит агент-потребитель.

---

## 8. Рекомендуемый стек (референс, можно менять)

Контракт §3–§4 первичен; стек — на выбор инструмента сборки. Референс для
«одно маленькое приложение»:

- **Ingest:** Python 3.11+, `boto3`, `PyYAML`, стандартный `sqlite3` (FTS5 встроен).
- **Backend/API:** Python + FastAPI. Читает `index.db`; оригиналы отдаёт через
  presigned S3 URL. (CORS для дев-режима Vite.)
- **Frontend: Vue 3** (обязательно) — Vite + Vue 3 `<script setup>`, Vue Router,
  Pinia по вкусу. Markdown-рендер `markdown-it`, подсветка результатов поиска на
  клиенте. SPA обращается к REST §4. Тяжёлого — не надо, но именно Vue 3.
- **Деплой:** один контейнер, `index.db` рядом; при старте (или по крону)
  прогон `ingest`. Ключи S3 — через env.

При сравнении инструментов (Codex/Cursor/Bolt/Replit) каждый собирает по этой же
спеке — сравниваем UX читалки, качество поиска и как чисто получилось.

---

## 9. Известные пробелы в данных (для директора)

- **PDF-инструкций и превью-картинок препаратов в бакете нет** — ни файлами, ни в
  БД (таблица `images` привязана к чанкам/документам книг, связи с препаратами
  нет; в `drugs` колонки картинки нет). У препарата в MVP только текст инструкции.
  Вероятный источник ассетов — внешний бакет `cx96074-ai-data` либо пересбор
  скиллом; отложено по решению.
- `dvcstore/` в этом бакете — **чужой проект** (аналитика мясного/рыбного
  B2B-портала), к VetAI отношения не имеет, игнорируем.
- Оригинал PDF отсутствует у 5 из 24 датасетов, но это ожидаемо: статьи Вика
  (есть `source_url`), сводная таблица препаратов, справочные/тестовые датасеты.

---

## 10. Порядок работ (вехи)

**1-я итерация (эта сборка):**
1. **M1 — Ingest + индекс:** `ingest/` строит `index.db` (sources, pages, toc,
   preparations из `drugs`+galen, diseases) из S3. Проверка: счётчики строк.
2. **M2 — API (FastAPI):** эндпоинты §4 поверх `index.db`, keyword-поиск,
   presigned PDF, CORS.
3. **M3 — Веб-читалка (Vue 3):** сводка → источник (оглавление + страницы) →
   поиск внутри книги с переходом на страницу.
4. **M4 — Фарма + диагностика:** списки с фильтрами и карточки.

**2-я итерация:**
5. **Skill для агента:** `skill/SKILL.md` + подключение к agent (стиль
   `vetdb sources/search`).
6. **Фаза 2:** semantic/hybrid из векторов дампов; обогащение оглавлений из
   `source_document.contents`; (при доступе) ассеты препаратов.
```
