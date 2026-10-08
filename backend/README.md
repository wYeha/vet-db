# VetAI Backend (FastAPI)

REST-API поверх готового `data/index.db` (SQLite + FTS5). Реализует контракт
SPEC §4, keyword-поиск через FTS5, presigned S3 URL для оригиналов PDF.

## Установка

```bash
pip install -r backend/requirements.txt
```

## Переменные окружения

| Переменная      | Обязательна | Дефолт                                             | Назначение                         |
|-----------------|-------------|----------------------------------------------------|------------------------------------|
| `DB_PATH`       | нет         | `<repo>/data/index.db`                             | путь к индексу (открывается ro)    |
| `HISTORY_DB_PATH`| нет        | `<repo>/data/history.db`                           | writable-БД истории чатов (WAL)    |
| `AK` / `SK`     | для /pdf    | —                                                  | S3 access/secret key               |
| `S3_ENDPOINT`   | нет         | `https://s3.twcstorage.ru`                         | endpoint бакета                    |
| `S3_BUCKET`     | нет         | `lz810806-ai-data`                                 | имя бакета                         |
| `S3_REGION`     | нет         | `ru-1`                                             | регион                             |
| `PDF_URL_TTL`   | нет         | `300`                                              | TTL presigned-ссылки, сек          |
| `CORS_ORIGINS`  | нет         | `http://localhost:5173,http://127.0.0.1:5173`      | origins фронта (через запятую)     |
| `LLM_BASE_URL`  | для /chat   | —                                                  | OpenAI-совместимый base URL (router.ai) |
| `LLM_API_KEY`   | для /chat   | —                                                  | ключ модели (секрет, не в коде)    |
| `LLM_MODEL`     | для /chat   | —                                                  | имя модели                         |
| `LLM_TIMEOUT`   | нет         | `30`                                               | таймаут запроса к модели, сек      |
| `LLM_MAX_TOKENS`| нет         | `700`                                              | лимит токенов ответа               |
| `LLM_TEMPERATURE`| нет        | `0.1`                                              | температура                        |
| `LLM_MAX_TOOL_CALLS`| нет     | `3`                                                | лимит итераций function-calling    |
| `LLM_TOOL_MODE` | нет         | `inline`                                           | `inline` (keywords→FTS→ответ) \| `tools` |
| `LLM_DAILY_BUDGET_RUB`| нет   | `100`                                              | суточный бюджет-гард, ₽            |
| `LLM_RUB_PER_1K_TOKENS`| нет  | `0.2`                                              | оценка стоимости 1К токенов, ₽    |
| `LLM_USE_ONTOLOGY`| нет      | `1`                                                | подмешивать карту онтологии в inline-чат (0=выкл) |
| `LLM_DISABLE_REASONING`| нет | `1`                                              | отключить reasoning модели (deepseek-flash: иначе `content` пустой; 0=не отключать) |

Ключи S3 и LLM в коде не хранятся; секреты — только в env/`backend/.env`
(в `.gitignore`). Без `LLM_*` эндпоинт `/api/chat` отдаёт 503. Для локальной разработки можно создать `.env` в корне
репозитория (см. `.env.example`) — он подхватится через python-dotenv.

## Запуск (dev)

```bash
# из корня репозитория
uvicorn app.main:app --reload --app-dir backend --port 8000
# либо
cd backend && uvicorn app.main:app --reload --port 8000
```

Проверка: <http://localhost:8000/api/health>, Swagger UI: <http://localhost:8000/docs>.

## Эндпоинты (SPEC §4)

| Метод | Путь                                | Описание                                              |
|-------|-------------------------------------|-------------------------------------------------------|
| GET   | `/api/health`                       | статус, путь к БД, признаки настроенных S3 и LLM      |
| GET   | `/api/sources`                      | список источников; фильтры `species`, `kind`, `q`     |
| GET   | `/api/sources/{id}`                 | карточка источника                                    |
| GET   | `/api/sources/{id}/toc`             | оглавление                                            |
| GET   | `/api/sources/{id}/pages/{index}`   | одна страница markdown (0-based)                      |
| GET   | `/api/sources/{id}/pdf`             | 302 redirect на presigned PDF (если есть pdf_s3key)   |
| GET   | `/api/search`                       | поиск; `q`,`scope`,`source_id`,`mode`,`limit`         |
| GET   | `/api/preparations`                 | список; `drug_class`,`animal`,`q`,`origin`,`limit`,`offset` |
| GET   | `/api/preparations/{id}`            | карточка препарата + instruction_md                   |
| GET   | `/api/diseases`                     | список болезней; фильтр `species=avian\|swine`        |
| GET   | `/api/diseases/{id}`                | карточка болезни (data — структура из yml)            |
| POST  | `/api/chat`                         | чат «найти источник»; `mode=ontology` (дефолт, LLM над картой онтологии) / `vector` (→501) / `None` (LLM над FTS); 503 без ключа; принимает необязательный `conversation_id`, возвращает его в ответе |
| GET   | `/api/conversations`                | список бесед (новые сверху); `limit` (≤200), `offset` |
| GET   | `/api/conversations/{id}`           | сообщения беседы по порядку; 404 если беседы нет      |

## Заметки

- **Поиск.** Режим `keyword` (FTS5). `mode=semantic\|hybrid` → 501 с текстом
  «фаза 2» (не 500). Пользовательский `q` санитизируется в безопасную MATCH-строку.
- **Внутри книги.** `GET /api/search?source_id=<id>&scope=books` — режим Ctrl+F.
- **Фарма-фильтры.** `drug_class` / `animal` заполнены только у `origin='drugs'`
  (79 записей); для galen они пустые — фильтр ожидаемо отсекает galen.
- **PDF.** Ключ S3 подписывается в исходном виде (cp1251/utf8 не трогаем). Без
  заданных `AK`/`SK` эндпоинт `/pdf` вернёт 503.
- **Чат «найти источник».** `POST /api/chat`
  `{message, history?, conversation_id?, mode?}` → `{answer, hits, usage,
  conversation_id}`. Поле `mode` (три-экранная модель поиска):
  - `mode=ontology` (**дефолт**) — ретрив по «карте онтологии» (`search_ontology`,
    без контентного FTS): модель подсказывает, какую книгу/главу/страницу смотреть.
  - `mode=vector` — заглушка векторного поиска: **501** ДО бюджет-гарда и вызова LLM
    (бюджет не тратится).
  - `mode` не задан (`None`) — старый путь: модель зовёт наш FTS-поиск (inline или
    function calling), отбирает хиты и даёт короткий ответ со ссылками. Фронт этот
    путь не использует.
  Без `LLM_*` → 503; при исчерпании суточного бюджета → 429; ошибка LLM → 502.
  Контент/аннотации из ретрива подаются модели как ДАННЫЕ (анти-инъекция OCR).
  Заголовок Authorization не логируется.
- **История чатов.** Хранится в отдельной writable-БД `data/history.db` (SQLite,
  WAL), НЕ в `index.db` (тот read-only и пересобирается из S3). После успешного
  ответа user- и assistant-реплики (с hits/usage) сохраняются best-effort: ошибка
  записи логируется, но НЕ роняет ответ чата. Схема (`conversations`, `messages`)
  создаётся при старте (`CREATE TABLE IF NOT EXISTS`). Путь — env `HISTORY_DB_PATH`.
  Осознанные ограничения MVP: авторизации нет → **история общая на всех**
  (изоляции пользователей нет); **удаления нет** (никаких разрушающих публичных
  эндпоинтов — «любой стирает всю историю» недопустимо); объём истории не
  ограничивается (ретеншн — вне scope).
