# План реализации MVP «База знаний VetAI» (1-я итерация)

> Источник истины: `SPEC.md` в корне. Этот план строго следует ему.
> Код пишет worker — здесь только декомпозиция, решения и риски.
> Дата: 2026-09-15.

## Цель и контекст

Собрать веб-читалку ветеринарной базы знаний для человека поверх уже готового
тонкого индекса `data/index.db` (SQLite + FTS5), собранного из S3 скриптом
`ingest/ingest.py`. S3 — источник правды, данные не мигрируем и не пересчитываем.

**В scope 1-й итерации:** ingest (валидация/точечная доработка), FastAPI REST
(эндпоинты SPEC §4), Vue 3 фронтенд (экраны SPEC §6), keyword-поиск (FTS5).

**ВНЕ scope (не планируем):** skill/эндпоинты для агента, семантический/векторный
поиск (`mode=semantic|hybrid`), превью-картинки и PDF препаратов.

### Готово и переиспользуется
- `ingest/ingest.py` — рабочий, строит `data/index.db` из S3.
- `data/index.db` (~101 МБ) уже собран: sources=23, pages=5593, toc=9572,
  preparations=2438 (79 drugs + 2359 galen), diseases=22.

### КРИТИЧНО: реальная схема FTS (из ingest.py, отличается от SPEC §3)
API обязан опираться на фактическую схему, а не на §3:
- `pages_fts(markdown, source_id UNINDEXED, source_title UNINDEXED, page_index UNINDEXED)`
  — автономный FTS (НЕ `content='pages'`). Хиты сразу содержат source_id/page_index.
- `preparations_fts(trade_name, generic_name, drug_class, target_animals, instruction_md, prep_id UNINDEXED)`.
- `diseases_fts(name, body, disease_id UNINDEXED)` — колонка называется `body`, не `data_json`.
- Базовые таблицы: `sources, pages, toc, preparations, diseases` — как в §3.
- Фильтры `drug_class`/`target_animals` заполнены только у `origin='drugs'` (79 шт);
  у galen они пустые — фильтр по ним молча отсекает galen (ожидаемо по §3).

---

## Предлагаемая структура репозитория

```
vet-DB/
├── SPEC.md                      # уже есть
├── data/index.db               # уже есть (в .gitignore, не коммитить 101 МБ)
├── ingest/
│   ├── ingest.py               # есть, доработки по M1
│   └── requirements.txt        # новый: boto3, PyYAML
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app, CORS, роутеры, /api/health
│   │   ├── config.py          # env: DB_PATH, S3_*, CORS origins
│   │   ├── db.py              # подключение к index.db (read-only), connection per-request
│   │   ├── s3.py             # presigned URL для PDF (boto3)
│   │   ├── schemas.py       # Pydantic-модели ответов §4
│   │   └── routers/
│   │       ├── sources.py      # /api/sources*, /toc, /pages, /pdf
│   │       ├── search.py       # /api/search (FTS5)
│   │       ├── preparations.py # /api/preparations*
│   │       └── diseases.py     # /api/diseases*
│   ├── requirements.txt        # fastapi, uvicorn, boto3
│   └── README.md               # как запустить (env, uvicorn)
├── frontend/
│   ├── index.html
│   ├── package.json            # vue3, vue-router, pinia?, markdown-it, vite
│   ├── vite.config.js          # proxy /api -> localhost:8000 (dev)
│   └── src/
│       ├── main.js, App.vue, router.js
│       ├── api/client.js       # обёртка fetch над /api
│       ├── components/
│       │   ├── MarkdownView.vue     # markdown-it рендер
│       │   ├── SearchBox.vue
│       │   ├── SearchResults.vue
│       │   └── TocTree.vue
│       └── views/
│           ├── HomeView.vue          # экран 1: сводка + фильтры + глоб.поиск
│           ├── ReaderView.vue        # экран 2: читалка (toc + страница + Ctrl+F)
│           ├── SearchView.vue        # экран 3: глобальный поиск
│           ├── PharmaView.vue        # экран 4: список препаратов + фильтры
│           ├── PreparationView.vue   # экран 4: карточка препарата
│           ├── DiseasesView.vue      # экран 5: список болезней
│           └── DiseaseView.vue       # экран 5: карточка болезни
└── .env.example                # AK/SK/S3_* — только пример, без секретов
```

---

## M1 — Ingest: валидация и точечная доработка

Основа готова; здесь проверяем и закрываем пробелы, НЕ переписываем.

- [~] Прогнать ingest на тестовом наборе (`GALEN_LIMIT=50`) — НЕ гонял: `data/index.db`
      уже собран с эталонными счётчиками; полный прогон лезет в S3 (нет ключей). Схема
      сверена чтением существующей БД.
- [x] Вынести `ingest/requirements.txt` (boto3, PyYAML) для воспроизводимости.
- [x] Проверить mojibake: строки в БД — корректный UTF-8 (проверено байтами:
      title/trade_name/имена статей читаемы). Терминал рендерит cp866 — артефакт консоли,
      не данных. Ключи S3 хранятся в исходном виде, нормализация не требуется.
- [x] `toc`: заголовки `#`/`##` извлекаются, `page_index` 0-based совпадает с pages
      (проверено на source 1). Шум оставлен как есть для MVP.
- [x] `pdf_s3key` заполнен у 19 из 23 (NULL у avian_biocheck, pcr_test, обеих статей ВИК).
- [x] Задокументировано в `ingest/README.md`: env, запуск, эталонные счётчики.
- [~] `source_url` для статей ВИК в данных отсутствует → оставлен NULL (фронт учитывает).

**Файлы:** `ingest/ingest.py` (точечно), `ingest/requirements.txt`, `ingest/README.md`.

---

## M2 — Backend: FastAPI API поверх index.db

Открывать `index.db` в режиме read-only (`file:...?mode=ro`, `uri=True`),
соединение на запрос (SQLite + потоки FastAPI). Все ответы — JSON, Pydantic-схемы.

- [x] **Каркас:** `main.py` с FastAPI, CORS (origins из env, для dev-Vite),
      `/api/health`, подключение роутеров. `config.py` читает env (DB_PATH, S3_*).
- [x] **db.py:** соединение read-only + `sqlite3.Row`; `build_match_query()` —
      токенизация ввода в безопасную FTS5 MATCH-строку (спецсимволы не роняют парсер,
      проверено). Плюс Unicode-aware `ulower()` для регистронезависимых LIKE по кириллице.
- [x] **GET /api/sources** — фильтры `species`, `kind`, `q` (ulower LIKE по title);
      `has_pdf = pdf_s3key IS NOT NULL`. Ответ по §4.
- [x] **GET /api/sources/{id}** — карточка: title, species, kind, num_pages,
      `pdf_url` (`/api/sources/{id}/pdf` или null), source_url, description, has_pdf.
- [x] **GET /api/sources/{id}/toc** — `[{title, level, page_index}]` ORDER BY page_index, id; 404 если нет источника.
- [x] **GET /api/sources/{id}/pages/{index}** — одна страница markdown + num_pages;
      404 если вне диапазона.
- [x] **GET /api/sources/{id}/pdf** — presigned S3 URL (boto3, TTL 300с) → 302 redirect;
      404 если нет pdf_s3key; 503 если S3-ключи не заданы. Реализовано в `s3.py`.
      (Живой S3 не дёргал — нет ключей; код presign корректен.)
- [x] **GET /api/search** — параметры `q`, `scope=all|books|pharma|diseases`,
      `source_id` (Ctrl+F внутри книги), `mode` (только `keyword`; `semantic|hybrid`
      → 400/501 с понятным сообщением «фаза 2»), `limit` (default 20):
      - books → `pages_fts` (при `source_id` — фильтр по колонке source_id), тип `page`,
        поля source_id/source_title/page_index, `snippet()` FTS5 с подсветкой.
      - pharma → `preparations_fts`, тип `preparation`, ref_id=prep_id, title=trade_name.
      - diseases → `diseases_fts`, тип `disease`, ref_id=disease_id, title=name.
      - `scope=all` объединяет и сортирует по `rank`/score с общим `limit`. Готово.
- [x] **GET /api/preparations** — фильтры `drug_class`, `animal` (ulower LIKE по
      target_animals), `q` (через FTS), `origin`, `limit` (default 50) + `offset`. Краткие поля §4.
- [x] **GET /api/preparations/{id}** — все поля + `instruction_md`; 404 если нет.
- [x] **GET /api/diseases** — фильтр `species=avian|swine`; `[{id, slug, species, name}]`.
- [x] **GET /api/diseases/{id}** — `data_json` парсится в объект `data`, полная структура.
- [x] **README backend:** запуск `uvicorn app.main:app`, env (DB_PATH, AK/SK, S3_*, CORS).

**Файлы:** всё в `backend/` (см. структуру). Секреты — только через env.

---

## M3 — Frontend: Vue 3 читалка

Vite + Vue 3 `<script setup>` + Vue Router. Markdown — `markdown-it`.
Dev-прокси `/api` на бэкенд (снимает CORS в деве), либо base-URL из env.

- [x] **Каркас:** Vite+Vue3 `<script setup>`, vue-router (hash-history), `api/client.js`
      (fetch, базовый URL из `import.meta.env`). `MarkdownView.vue` на markdown-it.
- [x] **Экран 1 — Home (`HomeView`):** список из `/api/sources`, группировка по
      `species` (свиньи/птица/КРС/общие), фильтры вид+тип+название, глоб.поиск в topbar
      (App.vue → SearchView). Карточка → ReaderView.
- [x] **Экран 2 — Reader (`ReaderView`):** слева `TocTree` (`/toc`), справа страница
      (`/pages/{index}`, markdown), навигация ‹ N/M ›. Поиск внутри книги
      (`/api/search?source_id=...`) → список страниц → клик = переход+подсветка на клиенте.
      Кнопка «Оригинал (PDF)» (скрыта если `has_pdf=false`; иначе — `source_url`).
- [x] **Экран 3 — Search (`SearchView`):** `/api/search?scope=all`, хиты по типам
      (страницы/препараты/болезни); у страниц «Источник → стр. N» со ссылкой + `?page=`;
      сниппет с подсветкой (`<mark>` из backend).
- [x] **Экран 4 — Pharma (`PharmaView` + `PreparationView`):** список с фильтрами
      drug_class/animal/поиск/origin + «Показать ещё» (offset); карточка — рендер
      `instruction_md` + поля. В UI отмечено, что class/animal применимы к origin=drugs.
- [x] **Экран 5 — Diseases (`DiseasesView` + `DiseaseView`):** список по
      `species=avian|swine`; карточка — рекурсивный рендер `data` (StructValue.vue) с рус. лейблами секций.
- [x] **Общее:** состояния загрузки/ошибок/пустого результата во всех views; типографика в styles.css.

**Файлы:** всё в `frontend/` (см. структуру).

---

## M4 — Фарма + диагностика (финализация)

Бэкенд-эндпоинты сделаны в M2, фронт-экраны в M3 — здесь довести UX и краевые случаи.

- [x] Пагинация/лимиты в списках препаратов: `limit`/`offset` в API + «Показать ещё» на фронте.
- [x] Рендер galen `instruction_md` (markdown с `## секциями`) через MarkdownView.
- [x] Парсинг `data_json` болезней: backend отдаёт `data` объектом, StructValue.vue
      рекурсивно рендерит строки/списки/объекты (проверено на структуре yml).
- [x] Пустые поля (generic_name, drug_class у galen) не ломают UI — условный рендер + `v-if`.

---

## Риски и на что обратить внимание

- **Схема FTS ≠ SPEC §3.** Использовать реальные имена колонок из ingest.py
  (`source_id/page_index` в pages_fts, `body` в diseases_fts, `prep_id/disease_id`).
  Иначе запросы к FTS упадут. — Главный риск.
- **Mojibake в ключах S3** (cp1251→utf8). Затрагивает presigned PDF и имена статей.
  Ключ передавать в S3 в исходном (байтовом) виде; нормализовать только отображение.
- **Presigned PDF.** Ключ может содержать не-ASCII/пробелы — boto3 корректно
  подпишет; проверить, что endpoint (twcstorage) отдаёт файл по подписи. TTL коротко.
- **FTS5 injection/парсер.** Пользовательский `q` с символами (`"`, `*`, `:`, `-`)
  может ронять MATCH. Санитизировать/квотировать запрос.
- **CORS Vite↔FastAPI.** В деве проще Vite-proxy `/api`; в проде — раздавать
  собранный фронт тем же процессом или задать CORS origins из env.
- **Размер index.db (~101 МБ).** Не коммитить в git; открывать read-only;
  соединение на запрос (SQLite не любит шаринг между потоками).
- **Поиск внутри книги = FTS с фильтром source_id.** Убедиться, что pages_fts
  хранит source_id как UNINDEXED-колонку и фильтр по ней работает вместе с MATCH.
- **Галеновые фильтры пустые.** `drug_class`/`target_animals` есть только у drugs —
  UI не должен создавать ощущение «пусто/сломано» при фильтрации galen.
- **Изображения книг** (`parsed_images/`) в markdown могут содержать относительные
  пути — для MVP допустимо не рендерить картинки внутри страниц (в scope только текст).

---

## Критерии готовности MVP (Definition of Done)

- [x] Ingest воспроизводимо строит `index.db` с эталонными счётчиками
      (sources=23, pages=5593, toc=9572, preparations=2438, diseases=22) — сверено по готовой БД.
- [x] Все эндпоинты §4 (кроме отложенных skill/semantic) отвечают корректным JSON;
      `mode=semantic|hybrid` → 501 «фаза 2», не 500.
- [~] `/api/sources/{id}/pdf` — код presign корректен, 302 при наличии PDF, 404/503
      краевые. Живой S3 не дёргался (нет ключей), проверены статусы без S3.
- [x] Фронт: главная со списком/группировкой/фильтрами; читалка с оглавлением,
      постраничной навигацией и поиском внутри книги с переходом+подсветкой.
- [x] Глобальный поиск: хиты по типам, цитата «Источник → стр. N», рабочие ссылки, безопасные сниппеты.
- [x] Фарма: список с фильтрами/пагинацией + карточка с рендером `instruction_md`.
- [x] Диагностика: список птица/свиньи + карточка из структуры yml.
- [x] Секреты только через env; в репозитории нет ключей S3 (`.env.example` без значений).
- [x] Backend и frontend запускаются по README; `npm run build` и dev-прокси проверены.
```
