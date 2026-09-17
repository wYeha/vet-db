# VetDB skill — подключение

Навык `vetdb` даёт внешнему агенту (opencode / Cursor / Codex и т.п.) поиск по
базе знаний VetAI через REST API (SPEC §4). Аутентификации нет — это внутренний
инструмент; секретов навык не содержит.

## Настройка

Единственный параметр — базовый URL API в переменной окружения:

```bash
export VETDB_API_BASE=http://localhost:8011   # адрес запущенного backend
```

Backend поднимается из `backend/` (см. `backend/README.md`):

```bash
cd backend
uvicorn app.main:app --port 8011
```

Проверка живости:

```bash
curl -s "$VETDB_API_BASE/api/health"
```

## Примеры curl

Поиск источников:

```bash
curl -s "$VETDB_API_BASE/api/search?q=парвовироз&scope=all&limit=10"
```

Поиск только по книгам, в пределах одной книги:

```bash
curl -s "$VETDB_API_BASE/api/search?q=дегидратация&scope=books&source_id=1"
```

Оглавление и текст страницы (page_index 0-based):

```bash
curl -s "$VETDB_API_BASE/api/sources/1/toc"
curl -s "$VETDB_API_BASE/api/sources/1/pages/4"
```

Препараты и болезни:

```bash
curl -s "$VETDB_API_BASE/api/preparations?q=ампициллин&limit=5"
curl -s "$VETDB_API_BASE/api/preparations/12"
curl -s "$VETDB_API_BASE/api/diseases"
curl -s "$VETDB_API_BASE/api/diseases/3"
```

## CLI-обёртка (опционально)

`vetdb.py` — тонкий argparse-клиент над теми же эндпоинтами, вывод — JSON:

```bash
export VETDB_API_BASE=http://localhost:8011
python skill/vetdb.py search "парвовироз" --scope books --limit 5
python skill/vetdb.py sources --q инфекц
python skill/vetdb.py toc 1
python skill/vetdb.py page 1 4
```

## Ограничения

- Поиск только `mode=keyword` (FTS5). `semantic`/`hybrid` пока не реализованы
  (эндпоинт вернёт 501).
- Навык только находит и цитирует источники; выводы агент делает сам.
