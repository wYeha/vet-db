# VetAI — База знаний

Внутренняя ветеринарная база знаний (свиньи / птица / КРС): тонкий индекс
**SQLite + FTS5 поверх S3** и веб-интерфейс к нему. S3 — источник правды,
`data/index.db` — пересобираемая проекция (в git не хранится).

Поиск доступен тремя способами (отдельные экраны):

- **Полнотекст** — FTS5 по тексту книг, без модели («как Яндекс»);
- **Онтология** — поиск по *смысловым оглавлениям* (аннотации глав/книг): модель
  лишь превращает фразу в ключевые слова, экран отдаёт плашки-ссылки на нужную
  книгу/главу/страницу;
- **Вектор** — заглушка (семантический поиск по эмбеддингам — на будущее).

Плюс чат-ассистент и skill-эндпоинты для внешнего агента.

Подробно об устройстве — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Структура

| Каталог | Что | README |
|---|---|---|
| `ingest/` | сборка `data/index.db` из S3 | [ingest/README.md](ingest/README.md) |
| `backend/` | FastAPI REST + чат | [backend/README.md](backend/README.md) |
| `frontend/` | Vue3 SPA (читалка, поиск, фарма, диагностика, чат) | — |
| `ontology/` | смысловые оглавления: аннотации 23 книг + скрипты | — |
| `skill/` | навык для внешнего агента | [skill/README.md](skill/README.md) |

## Как развернуть с нуля

Нужны **Python 3.11+**, **Node.js 18+**, доступ к S3-бакету (ключи `AK`/`SK` — у
директора) и, для вкладок с моделью, ключ RouterAI (`LLM_*`).

### 1. Клонировать

```bash
git clone https://github.com/wYeha/vet-db.git
cd vet-db
```

### 2. Секреты — файл `.env` в корне

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Заполнить в `.env` как минимум `AK`/`SK` (S3), а для чата/онтологии ещё
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. Бэкенд подхватит корневой `.env`
автоматически (python-dotenv). Файл в `.gitignore` — ключи в репозиторий не
попадают.

### 3. Зависимости

```bash
pip install -r ingest/requirements.txt
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

### 4. Построить индекс из S3 (ingest)

Ingest `.env` НЕ читает — ключи передаём явно:

```bash
# bash
AK=... SK=... python ingest/ingest.py
```

```powershell
# Windows PowerShell
$env:AK="..."; $env:SK="..."; python ingest/ingest.py
```

Итог — `data/index.db` (~101 МБ, ~4 минуты, в git не коммитится). Эталон после
полного прогона: `sources=23`, `pages=5593`, `toc=9972`, `preparations=2438`,
`diseases=22` (детали и валидация — в [ingest/README.md](ingest/README.md)).

### 5. Загрузить смысловые оглавления (онтология)

```bash
python ontology/load_annotations.py
```

Идемпотентно, S3 не нужен: наполняет `sources.description`, `toc.summary` и
FTS-таблицу `ontology_fts` из `ontology/annotations/*.md` (23 описания книг + 625
аннотаций глав).

### 6. Запустить бэкенд

```bash
# из корня репозитория
uvicorn app.main:app --reload --app-dir backend --port 8000
```

`AK`/`SK` нужны для presigned-ссылок на PDF, `LLM_*` — для вкладок с моделью; если
они в корневом `.env`, инлайн не требуется. Проверка: `GET /api/health`.

### 7. Запустить фронтенд

```bash
cd frontend && npm run dev
```

Откроется на <http://localhost:5173> (dev-прокси ходит на бэкенд). Если бэкенд на
другом адресе — задать `VITE_API_TARGET`, например
`VITE_API_TARGET=http://127.0.0.1:8000 npm run dev`.

## Заметки

- **Секреты** — только в `.env`/окружении, в коде их нет (см. `.env.example`).
- **`data/*.db`** (`index.db`, `history.db`) в git не хранятся — индекс
  пересобирается из S3 шагами 4–5.
- **Тесты бэкенда**: `cd backend && python -m pytest`.
