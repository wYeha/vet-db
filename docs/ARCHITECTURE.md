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
  безопасная FTS5 MATCH-строка, неявный AND, префикс `*` последнему);
  `build_or_match_query(q)` (то же, соединение через `OR`). Санитизация: только
  словарные символы, каждый токен в кавычках — спецсимволы FTS не проходят.
- `s3.py` — `presign_pdf(key)` (boto3 generate_presigned_url), `S3NotConfigured`.
- `history.py` — writable history.db (WAL, busy_timeout), CRUD бесед/сообщений + `clear_all()`.
- `llm.py` — OpenAI-совместимый клиент RouterAI (httpx): `chat_completion(messages, tools)`,
  таймаут/ретраи (4xx не ретраит), `LLMNotConfigured`/`LLMError`; Authorization не логируется.
- `schemas.py` — Pydantic-модели ответов.
- Роутеры (`routers/`):
  - `sources.py` — `GET /api/sources` (фильтр q), `/{id}`, `/{id}/toc?origin=all|curated|markdown`,
    `/{id}/pages/{index}`, `/{id}/pdf` (302 на presigned S3).
  - `search.py` — `GET /api/search` (см. «Поиск»); `_search_books/_search_pharma/_search_diseases`,
    `_safe_snippet` (экранирование HTML + доверенный `<mark>`).
  - `preparations.py` — `GET /api/preparations` (фильтры drug_class/animal/q/origin), `/{id}`.
  - `diseases.py` — `GET /api/diseases?species=`, `/{id}`.
  - `chat.py` — `POST /api/chat` (см. «Поиск: чат»); бюджет-гард, 503/429/502; запись в history.
  - `conversations.py` — `GET /api/conversations`, `/{id}`, `POST /api/conversations/clear`.
- Тесты: `backend/tests/test_chat.py`, `test_history.py`.

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
- Хит: `{type, source_id, source_title, page_index, ref_id, title, snippet, score}`.

### 2. Чат-ассистент — `POST /api/chat` (агент RouterAI над поиском)
Модель НЕ пишет медицинских ответов — помогает НАЙТИ источник. Поле `mode` в теле
задаёт три-экранную модель поиска:
- **`mode=ontology`** (ДЕФОЛТ, экран «Онтология») — ретрив ТОЛЬКО по карте онтологии
  (`search_ontology`, без контентного FTS): `_extract_keywords` → `search_ontology` →
  LLM даёт навигационный ответ «смотри такую-то книгу/главу/страницу». Строки
  онтологии маппятся в `SearchHit(type="page")` (при `kind=book` — уровень книги,
  `page_index=null`). Обёрнуто в try/except `OperationalError` (старый `index.db` без
  `ontology_fts` не роняет режим). Обычный вызов `/api/chat` без `mode` идёт сюда.
- **`mode=vector`** (экран «Вектор») — заглушка: **501** ДО бюджет-гарда и любого
  вызова LLM (бюджет не тратим; фронт этот экран бэк не зовёт).
- **`mode` не задан (`None`)** — старый путь над контентным FTS (ниже). Достижим
  только явным `mode=None`; фронт его не использует. Полнотекст «как Яндекс» — это
  отдельный экран на `GET /api/search` (без LLM, см. §1).

Старый FTS-путь: режим `inline` (по умолчанию), т.к. нативный function-calling у
DeepSeek нестабилен (протекает разметка). Три шага в `chat.py::_run_inline`:
1. **Ключевые слова** (`_extract_keywords`): один дешёвый вызов LLM превращает вопрос
   в 2–5 поисковых слов. Вопрос подаётся как ДАННЫЕ (анти-инъекция — инструкции в нём
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
page_index`, для preparation/disease — `ref_id/title`; общее — `snippet/score`. Фронт
строит из этого ссылки: page→читалка `/source/{id}?page=`, preparation→`/pharma/{id}`,
disease→`/diseases/{id}`.

### Онтология (в работе) — «смысловые оглавления»
Дополнительный слой навигации (подход Рената, одобрен директором): на каждую книгу и
главу — краткая аннотация «о чём + ключевые факты/термины/латынь». Агент читает эту
«карту» и выбирает, где искать (без векторов). Генерируется Claude/субагентами (не
RouterAI). Пилот — в `ontology/PILOT.md`; хранение — `sources.description` (книга) +
`toc.summary` (глава).

---

## Как запускать (dev)
- Ingest: `AK=… SK=… python ingest/ingest.py` → строит `data/index.db`.
- Backend: `AK=… SK=… python -m uvicorn app.main:app --port 8011` (из `backend/`),
  `LLM_*` из `backend/.env`.
- Frontend: `VITE_API_TARGET=http://127.0.0.1:8011 npm run dev` (из `frontend/`) → :5173.
