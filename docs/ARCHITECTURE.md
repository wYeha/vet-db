# Архитектура VetAI (база знаний по ветеринарии свиней / птицы / КРС)

Внутренний инструмент: читалка первоисточников + поиск + ИИ-ассистент «найти
источник». Три части — **ingest** (S3 → индекс), **backend** (FastAPI API поверх
индекса), **frontend** (Vue 3 SPA). Плюс **skill** (навык для внешнего агента).

```
S3 (источник правды, ~15 ГБ) ──ingest──▶ data/index.db (SQLite+FTS5, read-only)
                                              ▲
                        data/history.db ◀── backend (FastAPI) ──▶ frontend (Vue3)
                        (история чатов,          │
                         writable, WAL)          └── RouterAI (DeepSeek) — только чат
```

Принцип: **S3 — хранилище правды**, `index.db` — тонкая пересобираемая проекция
(индекс), приложение читает её **read-only**. Векторов пока нет (см. «Поиск»).

---

## Хранилища

### `data/index.db` (SQLite + FTS5, read-only, ~100 МБ, в .gitignore)
Пересобирается из S3 скриптом ingest. Таблицы:
- `sources(id, slug, title, language, num_pages, pdf_s3key, source_url, description)` — книги/статьи (23).
- `pages(id, source_id, page_index, markdown)` — страницы (5593), текст в markdown.
- `toc(id, source_id, title, level, page_index, origin)` — оглавления. `origin`:
  `markdown` (заголовки `#`/`##`, извлечены из текста, есть `page_index`) или
  `curated` (курированные из дампов БД, чистые, но `page_index=NULL`).
- `preparations(id, origin, trade_name, generic_name, drug_class, dosage_form, route,
  target_animals, manufacturer, reg_number, instruction_md)` — препараты (2438:
  `drugs`=79 каталог ВИК с чистыми фильтрами, `galen`=2359 гос.реестр).
- `diseases(id, slug, species, name, data_json)` — болезни (22, из yml).
- FTS5 (автономные, tokenize=unicode61):
  `pages_fts(markdown, source_id, source_title, page_index)`,
  `preparations_fts(trade_name, generic_name, drug_class, target_animals, instruction_md, prep_id)`,
  `diseases_fts(name, body, disease_id)`.

### `data/history.db` (SQLite, writable, WAL, в .gitignore)
Отдельно от read-only index.db. История чатов, общая на всех (авторизации нет).
- `conversations(id, title, created_at, updated_at)`.
- `messages(id, conversation_id, role['user'|'assistant'], content, hits_json, usage_json, created_at)`.

---

## Ingest — `ingest/ingest.py`
Идемпотентно строит `index.db` из S3 (ключи `AK`/`SK` из env). Функции:
- `build_schema(cx)` — DROP+CREATE всех таблиц и FTS.
- `ingest_sources(cx)` — по `VetAI/knowledge/data/<датасет>/`: если есть json с
  ключом `pages` → книга (страницы + toc из заголовков `#`/`##`); иначе `.md` →
  статьи (файл = страница). Заголовок = имя папки (без выдуманной классификации).
- `ingest_drugs_table(cx)` — парс markdown-таблицы `data/drugs/drugs_table.md` (ВИК,
  чистые поля класс/животное).
- `ingest_galen(cx)` / `_fetch_galen` — ~2359 препаратов гос.реестра из
  `knowledge/galen/preparations/<N>/`. Название — из `metadata.json → product.name`
  (структурированный реестр), фолбэк на регэксп `general_info` и номер папки, НИКОГДА
  на производителя. Оттуда же `generic_name`/`drug_class`. Инструкция — из sections.json.
- `ingest_ontology(cx)` — курированные оглавления из дампов
  `database_data/dumps/**/source_document*.sql` (поле `contents`) → `toc(origin='curated')`.
  Сопоставление дамп→наши книги — по карте `SDOC_SLUG` (имена ru/en, транслит).
- `ingest_diseases(cx)` — болезни из `diagnostics/**/*.yml`.

Прочее: `ingest/requirements.txt` (boto3, PyYAML), `ingest/README.md`.

---

## Backend — `backend/app/` (FastAPI)
Открывает `index.db` read-only (`mode=ro`), соединение-на-запрос. Секреты — только env.

- `main.py` — приложение, CORS (origins из env), подключение роутеров, инициализация
  схемы history.db, `GET /api/health` (флаги: db_exists, history_db_*, s3_configured, llm_configured).
- `config.py` — env: `DB_PATH`, `HISTORY_DB_PATH`, `S3_*`/`AK`/`SK`, `PDF_URL_TTL`,
  `CORS_ORIGINS`, `LLM_*` (BASE_URL/API_KEY/MODEL/TIMEOUT/MAX_TOKENS/TEMPERATURE/
  MAX_TOOL_CALLS/TOOL_MODE/DAILY_BUDGET_RUB/RUB_PER_1K_TOKENS), `LLM_CONFIGURED`.
- `db.py` — read-only соединение к index.db; `build_match_query(q)` (токены →
  безопасная FTS5 MATCH-строка, неявный AND; префикс `*` на КАЖДЫЙ токен после грубой
  обрезки окончания: len>=7 → -2, len>=5 → -1, иначе без обрезки; числа и токены <3
  символов — точные, без префикса; смешанные «b12» — без обрезки; дедуп термов);
  `build_or_match_query(q)` (те же термы через `OR`, кап 6 на уникальные). **Известное
  ограничение:** беглые гласные/чередования не ловятся («станок» → `стано*` не найдёт
  «станка», зато «станка» → `станк*` найдёт «станке/станков»; «свинья» не найдёт «свиней»).
  Лат./англ. эквиваленты от LLM-шага компенсируют recall.
  **Компромисс коротких токенов:** токены из 3–4 символов получают префикс без обрезки
  (иначе «кур» не найдёт «куры», «корм» — «корма»). Цена — шум в `/api/search` и
  `/api/preparations` («кот»* → «котёл», «вит»* → «витамин»). «ё»/«е» не нормализуются. Санитизация: только
  словарные символы, каждый токен в кавычках — спецсимволы FTS не проходят.
- `s3.py` — `presign_pdf(key)` (boto3 generate_presigned_url), `S3NotConfigured`.
- `history.py` — writable history.db (WAL, busy_timeout), CRUD бесед/сообщений + `clear_all()`.
- `llm.py` — OpenAI-совместимый клиент RouterAI (httpx): `chat_completion(messages, tools)`,
  таймаут/ретраи (4xx не ретраит), `LLMNotConfigured`/`LLMError`; Authorization не логируется.
- `schemas.py` — Pydantic-модели ответов.
- `snippets.py` — `safe_snippet`, `SNIPPET_ARGS` (+ алиасы `_safe_snippet`, `_SNIP`), `_MARK_OPEN/_MARK_CLOSE` (экранирование HTML +
  доверенный `<mark>`); общий для `routers/search.py` (реэкспорт под теми же именами) и ядра.
- `ontology.py` — ядро онтологии (без импорта роутеров): `search_ontology` (FTS по
  `ontology_fts`) и этап 2 чата `refine_ontology_hits` (`_chapter_ranges`, `_best_page`, `_is_bibliography`,
  `_shift_fallback_start`), общий фолбэк-маппер `fallback_hit`.
- Роутеры (`routers/`):
  - `sources.py` — `GET /api/sources` (фильтр q), `/{id}`, `/{id}/toc?origin=all|curated|markdown`,
    `/{id}/pages/{index}`, `/{id}/pdf` (302 на presigned S3).
  - `search.py` — `GET /api/search` (см. «Поиск»); `_search_books/_search_pharma/_search_diseases`,
    `_safe_snippet` (импорт из `snippets.py`).
  - `preparations.py` — `GET /api/preparations` (фильтры drug_class/animal/q/origin), `/{id}`.
  - `diseases.py` — `GET /api/diseases?species=`, `/{id}`.
  - `chat.py` — `POST /api/chat` (см. «Поиск: чат»); бюджет-гард, 503/429/502; запись в history.
  - `conversations.py` — `GET /api/conversations`, `/{id}`, `POST /api/conversations/clear`.
- Тесты: `backend/tests/test_chat.py`, `test_history.py`, `test_ontology_refine.py`
  (этап 2 на in-memory SQLite), `test_search_real_index.py` (на реальном index.db, skip без него).

---

## Frontend — `frontend/src/` (Vue 3 + Vite)
SPA обращается к REST §. Dev: Vite-прокси `/api` → backend (env `VITE_API_TARGET`).
- `main.js`, `App.vue` (шапка/навигация), `router.js` (hash-режим), `styles.css`.
- `api/client.js` — `get()`/`post()` + методы `api.*` (sources/search/preparations/
  diseases/health/chat/conversations/clearConversations).
- `views/`: `HomeView` (список источников + поиск), `ReaderView` (оглавление + постранично
  + поиск внутри книги с подсветкой + PDF), `SearchView` (глобальный поиск),
  `PharmaView`/`PreparationView`, `DiseasesView`/`DiseaseView`,
  `ChatView` (ассистент + список бесед + «Новый чат» + очистка истории).
- `components/`: `MarkdownView` (markdown-it, html:false), `TocTree` (оглавление,
  устойчив к `page_index=NULL`), `StructValue` (рендер структуры болезни),
  `ConfirmModal` (переиспользуемая модалка: пропсы title/message/buttons).

---

## Skill — `skill/`
`SKILL.md` (описание REST-эндпоинтов в стиле `vetdb` для подключения внешнего агента
opencode/Cursor), `README.md`, `vetdb.py` (CLI-обёртка над §, base URL из env).

---

## КАК РАБОТАЕТ ПОИСК ПО БАЗЕ ЗНАНИЙ

Поиск — **полнотекстовый по ключевым словам (FTS5), НЕ векторный/смысловой.** Вектора
qwen3 есть в дампах, но пока не подключены (будущий 2-й слой). «Ум» дают два дешёвых
шага модели вокруг поиска (подход «модель над поиском», как хотел директор).

### 1. Обычный поиск — `GET /api/search` («как Яндекс»)
Чистый FTS, без LLM. Возвращает список хитов, выводов не пишет.
- Параметры: `q`, `scope=all|books|pharma|diseases`, `source_id` (поиск внутри книги),
  `mode=keyword` (`semantic|hybrid` → 501, не реализовано), `limit`.
- Строит MATCH-строку (`build_match_query`), гонит по нужным FTS-таблицам:
  `pages_fts` (тип `page`, с source_id/стр.), `preparations_fts` (`preparation`),
  `diseases_fts` (`disease`).
- Ранжирование — встроенный **BM25** FTS5 (`ORDER BY rank`); сниппет с подсветкой —
  `snippet()` FTS5, прогнанный через `_safe_snippet` (HTML-escape + только `<mark>`).
- Хит: `{type, source_id, source_title, page_index, ref_id, title, snippet, score, precise}`
  (`precise` — только чат `mode=ontology`, см. «Контракт хита»).

### 2. Чат-ассистент — `POST /api/chat` (агент RouterAI над поиском)
Модель НЕ пишет медицинских ответов — помогает НАЙТИ источник. Поле `mode` в теле
задаёт три-экранную модель поиска:
- **`mode=ontology`** (ДЕФОЛТ, экран «Онтология») — ретрив по карте онтологии:
  `_extract_keywords` (1 LLM) → `search_ontology` (≤8 строк: книга/глава + старт) →
  `refine_ontology_hits` (этап 2, `backend/app/ontology.py`, только SQL). Ответ модели
  пустой — наверх уходят плашки `SearchHit(type="page")`. Обычный вызов `/api/chat`
  без `mode` идёт сюда.
  **Этап 2 — от начала главы к странице-фрагменту.** Для каждой строки онтологии —
  FTS по `pages_fts` внутри диапазона страниц главы `[start-1, end+1)` (запас
  `_RANGE_MARGIN=1`, нижняя граница ≥ 0). Конец главы: `ontology_fts.page_end`, если
  колонка есть и не NULL; иначе минимальный старт другой главы того же источника
  строго больше текущего; иначе `sources.num_pages`; иначе без верхней границы
  (`end<=start` → `start+1`). Запрос: сначала AND (`build_match_query`), при пустом
  результате — OR (`build_or_match_query`). Один запрос на ступень берёт до
  `_CANDIDATES=60` кандидатов по BM25, выбирается первый **не список литературы**
  (`_is_bibliography`: ≥3 строк-цитат «год + начало библиографической записи» и ≥50%
  содержательных строк — Harvard/Vancouver/«И.О. Фамилия»/«Орг (2019)», рус. и лат.;
  плотная латынь литературы иначе выигрывает BM25). Если в ступени только литература —
  она считается пустой.
  Найдено → `page_index` = эта страница, сниппет из тела книги (`_safe_snippet`,
  только доверенный `<mark>`), `precise=true`; не найдено → фолбэк: старт главы +
  сниппет аннотации, `precise=false` (фронт дописывает «(начало главы)»). Если старт
  главы (из-за ±1) попал на литературу предыдущей главы, а следующая страница — нет,
  фолбэк сдвигается на +1 (точечный запрос, только для фолбэков).
  `kind=book` и главы без старта (`page_index` NULL) ищут по всему источнику с нижней
  границей «первая известная глава − 1» (отсекает титул/печатное оглавление) и
  подписываются главой, в чей диапазон попала страница. Дедуп по
  `(source_id, page_index)` в порядке онтологии (book-хит, совпавший со страницей
  главы, получает её название); `score` и порядок — от онтологии. Бюджет: PRAGMA + 2
  вспомогательных запроса (колонки перечислены явно, наличие `page_end` — через
  `PRAGMA table_info`) + ≤2 FTS на строку (кэш в пределах вызова) + ≤1 точечный запрос
  на фолбэк; в логе `ontology refine: rows= fts= fallbacks= bib_skipped= ms=`. Деградация: старый `index.db` без `ontology_fts` →
  пустая карта; `OperationalError` в этапе 2 (нет `pages_fts`) → прежний маппинг строк
  онтологии. `/api/ontology/search` (внешний скилл) этап 2 не использует, его контракт
  и SQL не менялись. Известное ограничение: детектор литературы эвристический
  (по формату строк) — нестандартно оформленные списки могут не распознаться.
- **`mode=vector`** (экран «Вектор») — заглушка: **501** ДО бюджет-гарда и любого
  вызова LLM (бюджет не тратим; фронт этот экран бэк не зовёт).
- **`mode` не задан (`None`)** — старый путь над контентным FTS (ниже). Достижим
  только явным `mode=None`; фронт его не использует. Полнотекст «как Яндекс» — это
  отдельный экран на `GET /api/search` (без LLM, см. §1).

Старый FTS-путь: режим `inline` (по умолчанию), т.к. нативный function-calling у
DeepSeek нестабилен (протекает разметка). Три шага в `chat.py::_run_inline`:
1. **Ключевые слова** (`_extract_keywords`): один дешёвый вызов LLM превращает вопрос
   в 1–5 специфичных терминов (болезни, возбудители, препараты, методы) в начальной
   форме + лат./англ. эквиваленты; общие слова («дозировка», «лечение») и виды животных
   запрещены. Вопрос подаётся как ДАННЫЕ (анти-инъекция — инструкции в нём
   игнорируются).
2. **Поиск** (`_run_search`): FTS по ключевым словам. Сначала строгий AND
   (`build_match_query`); если сильных хитов **меньше 3** — дополнительно OR
   (`build_or_match_query`) и **мёрж** (AND-хиты выше, OR-добавка ниже, дедуп по
   `_hit_key`, до 10). Область — книги + препараты + болезни.
3. **Ответ** (второй вызов LLM): найденные хиты отдаются как ДАННЫЕ (+ пометка, если
   были OR-хиты). Модель оценивает релевантность: по теме → короткий ответ + цитаты
   «источник → стр. N»; слабо связано → честно «точного совпадения не нашлось».
   Ничего не выдумывает — только реально найденные хиты.
- Модель: RouterAI (`https://routerai.ru/api/v1`, OpenAI-совместимый),
  `deepseek/deepseek-v4.1-flash`, ~0,07 ₽/запрос. Ключ/URL/модель — только env.
- Гарантии: нет ключа → **503**; суточный бюджет-гард → **429**; ошибка LLM → **502**
  (без утечки деталей); лимит итераций; контент из поиска — данные, не инструкции.
- После ответа реплики сохраняются в `history.db` (best-effort, не роняет ответ).

### Контракт хита (общий для поиска и чата)
`SearchHit`: `type=page|preparation|disease`, для page — `source_id/source_title/
page_index`, для preparation/disease — `ref_id/title`; общее — `snippet/score`.
Аддитивное поле `precise` (только чат `mode=ontology`): `true` — страница найдена FTS в
теле книги внутри главы, сниппет из тела; `false` — фолбэк (старт главы или уровень
книги, сниппет из аннотации); `null` — все прочие хиты (поиск, старый чат, история до
изменения). Для онтологии `title` — название главы (или «о книге»). Фронт
строит из этого ссылки: page→читалка `/source/{id}?page=`, preparation→`/pharma/{id}`,
disease→`/diseases/{id}`.

### Онтология (в работе) — «смысловые оглавления»
Дополнительный слой навигации (подход Рената, одобрен директором): на каждую книгу и
главу — краткая аннотация «о чём + ключевые факты/термины/латынь». Агент читает эту
«карту» и выбирает, где искать (без векторов). Генерируется Claude/субагентами (не
RouterAI). Пилот — в `ontology/PILOT.md`; хранение — `sources.description` (книга) +
`toc.summary` (глава).

Поиск — `ontology_fts(chapter_title, summary, source_id, source_slug, source_title,
page_index, kind, page_end)`, загружается `ontology/load_annotations.py` (идемпотентно,
индекс — пересобираемая проекция). Из заголовка аннотации `### Название (стр A–B)`
берутся `page_index = A-1` (старт главы) и `page_end = B` (exclusive-конец в той же
конвенции; NULL, если в заголовке одна страница или страниц нет; у `kind=book` — NULL).
`page_end` — последняя колонка, индексы колонок для `snippet()` не сдвинуты; во внешний
`/api/ontology/search` не отдаётся (SQL перечисляет колонки явно). Бэкенд работает и
без этой колонки (старый индекс) — тогда конец главы = следующий старт.

**Известное ограничение: конвенция страниц в аннотациях смешанная (0/1-based).** В
одних аннотациях A уже 0-based (`streptococcosis_dissertation`: «Глава 1 (стр 9–11)»
при `start=9` в `chapters/*.json` → после `-1` получается 8), в других 1-based
(`vic_articles_swine`: «(стр 1)» = page 0). Отсюда систематическая ошибка старта/конца
±1 стр. Не исправляется (сдвиг поменял бы `page_index` во внешнем
`/api/ontology/search`); в чате гасится запасом диапазона `_RANGE_MARGIN=1` с обеих
сторон. Исправление — отдельная задача (нормализовать A по аннотациям).

**Почему этап 2 не расширяет запрос словарём аннотации.** Глава уже выбрана потому,
что ключевые слова совпали с её аннотацией; билингвальность (рус. аннотация + англ.
тело) закрывается OR-ступенью, т.к. в keywords уже есть латынь. Термины `summary`
описывают тему главы, а не запрос: страница, «плотная по главе», была бы подана как
точный фрагмент (`precise=true`) и вводила бы в заблуждение. Честный фолбэк (начало
главы + аннотация с пометкой) полезнее ложноточного фрагмента.

---

## Как запускать (dev)
- Ingest: `AK=… SK=… python ingest/ingest.py` → строит `data/index.db`.
- Backend: `AK=… SK=… python -m uvicorn app.main:app --port 8011` (из `backend/`),
  `LLM_*` из `backend/.env`.
- Frontend: `VITE_API_TARGET=http://127.0.0.1:8011 npm run dev` (из `frontend/`) → :5173.
