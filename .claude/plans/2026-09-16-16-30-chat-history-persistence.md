# План: сохранение истории чатов (VetAI Knowledge Base)

Дата: 2026-09-16 16:30 · Ветка: master · Автор запроса: snovavzloman@gmail.com

## Цель и контекст

Сохранять историю переписки с чат-ассистентом «найти источник», чтобы она
переживала перезапуск сервера. Авторизации в проекте нет, поэтому история —
**единая общая база на всех пользователей** (внутренний инструмент, изоляции
пользователей нет — осознанное ограничение MVP).

Текущее состояние (изучено по коду):
- `POST /api/chat` (`backend/app/routers/chat.py`) принимает `{message, history?}`
  и возвращает `{answer, hits, usage}`. История сейчас передаётся клиентом в
  каждом запросе (stateless), нигде не хранится.
- `backend/app/db.py` открывает `index.db` строго в `mode=ro` (read-only,
  пересобирается из S3 скриптом ingest). Писать в него нельзя — записи терялись бы
  при пересборке.
- `backend/app/config.py` — все пути/секреты из env; `DB_PATH` → `data/index.db`.
- Фронт: `frontend/src/views/ChatView.vue` держит `messages` только в памяти
  вкладки; `frontend/src/api/client.js` — тонкая обёртка над REST.
- `.gitignore` уже игнорирует `data/*.db`, `data/*.db-journal`, `data/*.db-wal`,
  но **НЕ** `data/*.db-shm` (WAL создаёт и `-shm`) — нужно добавить.

## Ключевые архитектурные решения (worker их НЕ выбирает сам)

1. **Отдельная writable-БД `data/history.db`.** Не трогаем `index.db` (read-only,
   пересобирается). Новый модуль `backend/app/history.py` инкапсулирует:
   отдельное write-соединение, инициализацию схемы, CRUD. Существующее read-only
   соединение к index.db в `db.py` **не меняем** по сути.
2. **WAL + короткие транзакции.** При инициализации выполнять
   `PRAGMA journal_mode=WAL;` и `PRAGMA synchronous=NORMAL;`. Каждая операция
   записи — своя короткая транзакция (commit сразу). Соединение открывать
   с `check_same_thread=False` и на каждый запрос (как в db.py) ИЛИ единое
   с блокировкой — см. решение ниже.
   - Решение: **соединение-на-операцию** (открыть → записать/прочитать → закрыть),
     как уже сделано для index.db. Проще и безопаснее для пула потоков FastAPI,
     WAL допускает конкурентных читателей + одного писателя. `busy_timeout` (напр.
     5000 мс) через `PRAGMA` для устойчивости к параллельным записям.
3. **Схема БД:**
   ```
   conversations(
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     title TEXT,
     created_at TEXT NOT NULL,   -- ISO-8601 UTC
     updated_at TEXT NOT NULL
   )
   messages(
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     conversation_id INTEGER NOT NULL REFERENCES conversations(id),
     role TEXT NOT NULL CHECK(role IN ('user','assistant')),
     content TEXT NOT NULL,
     hits_json TEXT,             -- JSON-сериализация hits (только для assistant)
     usage_json TEXT,            -- JSON-сериализация usage (только для assistant)
     created_at TEXT NOT NULL
   )
   CREATE INDEX idx_messages_conv ON messages(conversation_id, id);
   ```
   Инициализация схемы при старте приложения (`CREATE TABLE IF NOT EXISTS`).
4. **Эндпоинты (аддитивно, контракт чата не ломаем):**
   - `POST /api/chat` расширить: принимать необязательный `conversation_id`.
     Логика: если `conversation_id` не задан/не найден → создать новую беседу
     (title = обрезанный текст первого сообщения). ПОСЛЕ успешного ответа LLM
     сохранить user-сообщение и ответ ассистента (с hits/usage). Вернуть
     `conversation_id` в теле ответа. Сохранение — best-effort: ошибка записи
     логируется, но НЕ роняет уже полученный ответ.
   - `GET /api/conversations?limit=&offset=` — список бесед, новые сверху
     (`ORDER BY updated_at DESC`), поля: id, title, created_at, updated_at,
     message_count. Пагинация (limit по умолчанию 50, максимум 200).
   - `GET /api/conversations/{id}` — все сообщения беседы по порядку
     (`ORDER BY id`), 404 если беседы нет. hits_json/usage_json десериализуются
     обратно в структуры.
5. **Безопасность/UX:** НИКАКИХ разрушающих публичных эндпоинтов в MVP (нет
   DELETE — «любой стирает всю историю» недопустимо). История общая и видна всем —
   зафиксировать в README как осознанное ограничение внутреннего инструмента.
6. **Frontend (`ChatView.vue`):** боковой список бесед, «Новый чат», загрузка
   выбранной беседы, активный `conversation_id` в localStorage.

## Декомпозиция (чеклист)

### M1 — writable history.db: инфраструктура
- [x] Шаг 1.1: В `config.py` добавить `HISTORY_DB_PATH` из env
      (`os.environ.get("HISTORY_DB_PATH", <data/history.db рядом с index.db>)`)
      по образцу `_default_db_path()`.
- [x] Шаг 1.2: Создать `backend/app/history.py`: функция `_connect()` (открывает
      `HISTORY_DB_PATH` НЕ в ro-режиме, `check_same_thread=False`,
      `row_factory=sqlite3.Row`); контекст-менеджер `connection()`; при первом
      подключении/на старте выставить `PRAGMA journal_mode=WAL`,
      `synchronous=NORMAL`, `busy_timeout=5000`.
- [x] Шаг 1.3: В `history.py` функция `init_schema()` — `CREATE TABLE IF NOT
      EXISTS` для conversations/messages + индекс. Идемпотентна.
- [x] Шаг 1.4: В `main.py` вызвать `history.init_schema()` при старте
      (startup-событие FastAPI или прямой вызов на import модуля роутера).
      Директорию `data/` при отсутствии создать (`os.makedirs(exist_ok=True)`).
- [x] Шаг 1.5: В `.gitignore` добавить `data/*.db-shm` (WAL создаёт `-shm`;
      `-wal` уже игнорируется).

### M2 — backend: сохранение + эндпоинты
- [x] Шаг 2.1: В `history.py` реализовать функции доступа:
      `create_conversation(title) -> id`,
      `add_message(conversation_id, role, content, hits_json=None, usage_json=None)`
      (внутри — обновление `conversations.updated_at`),
      `conversation_exists(id) -> bool`,
      `list_conversations(limit, offset) -> list` (с message_count через JOIN/подзапрос),
      `get_conversation_messages(id) -> list`. Каждая — короткая транзакция.
- [x] Шаг 2.2: В `schemas.py` добавить: поле `conversation_id: Optional[int]` в
      `ChatRequest`; поле `conversation_id: int` в `ChatResponse`; новые модели
      `ConversationListItem` (id, title, created_at, updated_at, message_count) и
      `ConversationMessage` (id, role, content, hits, usage, created_at) +
      `ConversationDetail`/список для ответов эндпоинтов.
- [x] Шаг 2.3: В `chat.py` в конце `chat()`: определить/создать беседу
      (title = `req.message[:80]` при создании), после формирования `ChatResponse`
      сохранить оба сообщения. hits сериализовать через
      `json.dumps([h.model_dump() for h in hits])`, usage — `usage.model_dump()`.
      Обернуть запись в try/except с `log.warning` — ошибка записи НЕ ломает ответ.
      Вернуть `conversation_id` в `ChatResponse`.
- [x] Шаг 2.4: Создать `backend/app/routers/conversations.py` с
      `GET /api/conversations` и `GET /api/conversations/{id}` (404 при отсутствии).
      Десериализация hits_json/usage_json через `json.loads` (никакого eval).
- [x] Шаг 2.5: Зарегистрировать роутер в `main.py`
      (`app.include_router(conversations.router)`).
- [x] Шаг 2.6: (опц.) В `/api/health` добавить `history_db_path`/`history_db_exists`
      для наблюдаемости.

### M3 — frontend
- [x] Шаг 3.1: В `client.js` добавить методы:
      `conversations(params)` → `GET /api/conversations`,
      `conversation(id)` → `GET /api/conversations/{id}`;
      расширить `chat` — передавать `conversation_id`.
- [x] Шаг 3.2: В `ChatView.vue` добавить боковую панель со списком бесед
      (загрузка через `api.conversations()` в `onMounted`), пустое состояние и
      обработку ошибок.
- [x] Шаг 3.3: Хранить активный `conversation_id` в `ref` + localStorage
      (ключ, напр. `vetai.chat.conversation_id`). При старте — восстановить.
- [x] Шаг 3.4: Клик по беседе → `api.conversation(id)` → отрисовать сообщения
      (маппинг role/content/hits в текущий формат `messages`); установить активный id.
- [x] Шаг 3.5: Кнопка «Новый чат» — очистить `messages`, сбросить активный id
      (localStorage), следующий `send()` создаст новую беседу на бэке.
- [x] Шаг 3.6: В `send()` передавать текущий `conversation_id`; из ответа
      сохранить вернувшийся `conversation_id`, обновить список бесед.
- [x] Шаг 3.7: Стили боковой панели в `ChatView.vue`/`styles.css` (не ломая
      существующую вёрстку).

### M4 — тесты и проверка
- [x] Шаг 4.1: Тесты в `backend/tests/` (мок LLM, изолированная временная
      history.db через monkeypatch `HISTORY_DB_PATH`/tmp_path):
      сохранение после `/api/chat`; создание беседы при отсутствии
      `conversation_id`; продолжение по существующему `conversation_id`;
      `GET /api/conversations` (порядок, message_count); `GET /api/conversations/{id}`
      (порядок сообщений, 404 для несуществующего); ошибка записи не роняет ответ.
- [x] Шаг 4.2: Убедиться, что существующие `test_chat.py` не сломались
      (контракт `ChatResponse` расширен, не изменён; проверить, что новые тесты
      используют временную БД, а не боевую).
- [x] Шаг 4.3: Обновить README (`backend/README.md`): описать history.db, env
      `HISTORY_DB_PATH`, новые эндпоинты и осознанное ограничение (общая история,
      нет изоляции пользователей, нет удаления в MVP).
- [x] Шаг 4.4: Обновить `.env.example` — добавить `HISTORY_DB_PATH` (опционально).

## Риски и митигации

- **Конкурентные записи SQLite / `database is locked`.** WAL + `busy_timeout`
  (5 c) + короткие транзакции (commit сразу) + соединение-на-операцию. Одновременный
  писатель один — для внутреннего инструмента достаточно.
- **Случайная запись в read-only index.db.** history.db — отдельный файл и
  отдельный модуль; db.py не трогаем в части ro-подключения. Проверить, что
  `chat.py` использует `history.*` для записи, а `connection()` (index.db) — только
  для поиска.
- **Падение при сохранении роняет ответ чата.** Сохранять ТОЛЬКО после успешного
  ответа LLM; всю запись обернуть try/except с логированием — пользователь всё равно
  получает `answer`/`hits`.
- **Рост базы истории.** В MVP не ограничиваем; отметить в README как известный
  момент. Возможная будущая мера — ретеншн/архивация (вне scope).
- **Отсутствие изоляции пользователей (общая история).** Осознанное ограничение
  внутреннего инструмента; зафиксировать в README. `conversation_id` в localStorage
  даёт «свой тред» только на уровне UX, не безопасности.
- **Инъекции через hits/usage.** Сериализация строго через `json.dumps`,
  десериализация через `json.loads` — никакого `eval`/`exec`. Параметры SQL — только
  через плейсхолдеры (`?`), без конкатенации.
- **`-shm` попадёт в git.** Добавить `data/*.db-shm` в `.gitignore` (Шаг 1.5).
- **Тесты пишут в боевую history.db.** Обязательно перенаправлять путь на
  `tmp_path` через monkeypatch и переоткрывать соединение/схему в фикстуре.

## Затронутые файлы

Создание:
- `backend/app/history.py`
- `backend/app/routers/conversations.py`
- `backend/tests/test_history.py` (или расширение существующих тестов)

Правка:
- `backend/app/config.py` — `HISTORY_DB_PATH`
- `backend/app/schemas.py` — `conversation_id`, новые модели
- `backend/app/routers/chat.py` — сохранение + возврат `conversation_id`
- `backend/app/main.py` — init схемы + include роутера conversations
- `backend/README.md` — документация и ограничения
- `.env.example` — `HISTORY_DB_PATH`
- `.gitignore` — `data/*.db-shm`
- `frontend/src/api/client.js` — методы conversations, `conversation_id` в chat
- `frontend/src/views/ChatView.vue` — список бесед, новый чат, localStorage
- `frontend/src/styles.css` — (при необходимости) стили панели

## Definition of Done

- [x] `POST /api/chat` создаёт/продолжает беседу и возвращает `conversation_id`;
      после ответа user- и assistant-сообщения (с hits/usage) сохранены в
      `data/history.db`.
- [x] История переживает перезапуск сервера (данные читаются из файла).
- [x] `GET /api/conversations` и `GET /api/conversations/{id}` работают
      (порядок, message_count, 404).
- [x] index.db остаётся строго read-only; поведение 503/429/502 и бюджет-гард не
      изменены; существующие тесты `test_chat.py` зелёные.
- [x] Фронт: список бесед, загрузка беседы, «Новый чат», активный тред в
      localStorage; пустые состояния и ошибки обработаны.
- [x] Ошибка записи истории не роняет ответ чата (проверено тестом).
- [x] README и `.env.example` обновлены; `data/*.db-shm` в `.gitignore`.
- [x] Никаких публичных разрушающих эндпоинтов.
