# Онтология-поиск, этап 2: от начала главы к конкретной странице-фрагменту

## Цель и контекст

Сейчас чат в `mode=ontology` (`backend/app/routers/chat.py::_run_ontology`) работает так:
keywords (`_extract_keywords`) → `search_ontology` → `_ontology_row_to_hit`. Плашка ведёт на
**начало главы** (`page_index` = старт главы), сниппет взят из аннотации.

Задача: для каждой главы-кандидата выполнить FTS по `pages_fts` **внутри диапазона страниц
этой главы**. Плашка должна вести на лучшую по BM25 страницу, а сниппет показывать фрагмент
**тела книги** (с `<mark>`, безопасно). Если в диапазоне ничего не нашлось, остаётся старт
главы и сниппет аннотации. Новых LLM-вызовов нет. `/api/ontology/search` не меняется.

Конвейер после задачи:
```
запрос → _extract_keywords (1 LLM)
       → search_ontology: ≤8 строк (книга/глава + старт)
       → refine_ontology_hits: 1 SQL на диапазоны + ≤8×(1–2) FTS по pages_fts в диапазоне
       → SearchHit(type="page", page_index = лучшая страница, snippet = фрагмент тела, precise=True|False)
```

### Факты о данных (проверены)
- `ontology_fts(chapter_title, summary, source_id, source_slug, source_title, page_index, kind)`
  хранит **только старт** главы. Конец не хранится.
- `ontology/load_annotations.py::parse` берёт из заголовка `### Название (стр A–B)` только A и
  пишет `page_index = A-1`. B выбрасывается.
- **Конвенция A в аннотациях смешанная.** В `streptococcosis_dissertation.md` «Глава 1 (стр 9–11)»,
  а в `chapters/*.json` `start=9`, то есть A уже 0-based, и после `-1` получается 8. В
  `vic_articles_swine.md` «(стр 1)» соответствует markdown-заголовку на page 0, то есть A 1-based
  и `-1` верно. Отсюда систематическая ошибка ±1 стр. → диапазон нужно брать с запасом.
- Формат B: `стр 9–11`, `стр. 14–33`, `(Глава 1, стр 3–18)`. Тире бывает `–`, `—` или `-`.
  Диапазон указан у 375 заголовков из 629. У остальных одна страница (`vic_articles_*`,
  `pcr_test`, `avian_biocheck`) или страниц нет вовсе («Раздел I…» в
  `antimicrobial_therapy_handbook.md`, там `page_index` NULL при `kind=chapter`).
- `ontology/chapters/<slug>.json` содержит `start/end` (end = старт следующей главы, exclusive) и
  `num_pages`, но только для курированных глав, а их названия не всегда совпадают с
  аннотациями. Как источник диапазонов для рантайма **не используем** (это файлы вне БД, нужен
  матч по названию).
- `pages_fts(markdown, source_id UNINDEXED, source_title UNINDEXED, page_index UNINDEXED)`:
  `page_index`/`source_id` вставлены как int (ingest.py:139), поэтому сравнения `>=`/`<` корректны.
- `sources.num_pages` есть.

## Архитектурные решения

1. **Конец главы (B).** Решение двухуровневое, бэкенд работает при любом состоянии БД:
   - **(основное, рантайм, без пересборки)** `end` = минимальный `page_index` другой главы того
     же источника, у которой старт строго больше текущего. Если такой нет, берём
     `sources.num_pages`. Если и его нет, верхней границы нет. Аннотации почти везде плоские
     и последовательные (проверено на strep, broiler, handbook), так что этого достаточно.
   - **(доработка загрузчика, отдельный шаг-скрипт)** `load_annotations.py` дополнительно
     парсит B и пишет его в новую колонку `page_end UNINDEXED` в `ontology_fts`. Значение
     приводится к той же конвенции, что и старт: inclusive-индекс B-1, то есть exclusive end = B.
     Бэкенд берёт `page_end`, если колонка есть и значение не NULL, иначе вычисляет next-start.
     Наличие колонки определяем через `row.keys()` в результате `SELECT *` (без try/except на
     схему). Пересборку `index.db` worker делает вручную запуском скрипта, бэкенд остаётся
     read-only.
   - Сдвиг старта (`A-1`) в этой задаче **НЕ трогаем**: это поменяло бы `page_index` фолбэка и
     внешний `/api/ontology/search`. Ошибку ±1 гасим запасом диапазона.
2. **Запас диапазона.** Ищем в `[start - 1, end + 1)`, нижняя граница не меньше 0. Константа
   `_RANGE_MARGIN = 1`. Если `end <= start`, то `end = start + 1`.
3. **Запрос второго этапа.** Сначала `build_match_query(keywords)` (AND). Если в диапазоне
   0 страниц, пробуем `build_or_match_query(keywords)` (OR). Если и так пусто, фолбэк.
   Берём одну лучшую страницу (`ORDER BY rank LIMIT 1`).
4. **Расширение словарём аннотации не делаем.** Обоснование:
   - глава уже выбрана, потому что ключевые слова совпали с её аннотацией. Билингвальность
     (рус. аннотация + англ. тело) закрывается OR-ступенью, ведь в keywords уже есть латынь;
   - термины `summary` описывают тему главы, а не запрос. Страница, «плотная по главе», была бы
     подана как «точный фрагмент» (`precise=True`) и ввела бы в заблуждение;
   - честный фолбэк (начало главы + аннотация с пометкой «начало главы») полезнее
     ложноточного фрагмента. Если понадобится, это будущая опция за флагом, не сейчас.
5. **kind=book и глава без старта (`page_index` NULL).** Ищем по всему источнику. Если у
   источника известны главы с `page_index`, нижняя граница = минимальный старт главы минус
   margin (отсекаем титул и печатное оглавление, которые выигрывают BM25 плотностью терминов).
   Найденную страницу подписываем названием главы, в чей диапазон она попала (диапазоны
   уже загружены, лишних запросов нет). Если ничего не попало: у book `title=None`, у главы
   без старта остаётся её `chapter_title`. Если страница не нашлась, поведение прежнее
   (`page_index=None` для book, title «о книге»).
6. **Контракт ответа.** `SearchHit` расширяется ровно на одно аддитивное поле:
   `precise: Optional[bool] = None`. `True` — страница найдена FTS в теле, сниппет из тела.
   `False` — фолбэк (старт главы или уровень книги, сниппет из аннотации). `None` — все
   прочие хиты (поиск, старый чат, история до изменения). Поле «старт главы» не добавляем:
   глава уже видна в title. Аннотацию на точной плашке **не показываем**, чтобы не
   перегружать: заголовок «книга → глава · стр. N» и фрагмент тела дают достаточно контекста.
   `score` = ontology score, порядок плашек = порядок онтологии (ранги AND/OR/разных запросов
   несравнимы).
7. **Фронт.** В `ChatView.vue::hitTitle` при `h.precise === false && h.page_index != null`
   к заголовку добавляется ` (начало главы)`. Больше ничего не меняется.
8. **Дедуп.** Ключ `(source_id, page_index)`, причём `page_index=None` тоже считается ключом.
   Побеждает первый в порядке онтологии. Исключение: если уже взятый хит был book-уровнем
   (title=None или «о книге»), а новый пришёл из главы, title у сохранённого заменяется на
   название главы. В `collected` уходят уже дедуплицированные хиты, `_add_hits` дополнительно
   страхует по `_hit_key`.
9. **Размещение кода.**
   - `_MARK_OPEN`, `_MARK_CLOSE`, `_SNIP`, `_safe_snippet` переезжают из `routers/search.py` в
     новый `backend/app/snippets.py`. `search.py` импортирует их оттуда под **теми же именами**,
     так что `from .search import _safe_snippet` продолжает работать. Ядро не должно
     импортировать роутер (принцип из `ontology.py`).
   - Логика этапа 2 живёт в `backend/app/ontology.py` рядом с `search_ontology` как публичная
     `refine_ontology_hits(db, rows, keywords) -> List[SearchHit]` и приватные
     `_chapter_ranges(db, source_ids)` и `_best_page(db, match, source_id, lo, hi)`. Это
     функции уровня модуля, чтобы тесты могли их monkeypatch'ить. Импорт `SearchHit` из
     `schemas` допустим, циклов нет.
   - `_ontology_row_to_hit` в `chat.py` остаётся как фолбэк-маппер (выставляет `precise=False`).
10. **`/api/ontology/search` не меняется.** SQL в `search_ontology` (`_SQL`) по-прежнему
    перечисляет колонки явно, поэтому `page_end` во внешний JSON не утекает. Параметр `refine`
    для внешнего скилла в эту задачу не входит (можно добавить позже, ядро уже переиспользуемо).
11. **Деградация.** В `_run_ontology` вызов `refine_ontology_hits` обёрнут в
    `try/except sqlite3.OperationalError`: при старом `index.db` или отсутствии `pages_fts` пишем
    лог и делаем старый маппинг `[_ontology_row_to_hit(o) …]`.

### Алгоритм `refine_ontology_hits` (для worker)
```
if not rows: return []
and_q = build_match_query(keywords); or_q = build_or_match_query(keywords)
ranges = _chapter_ranges(db, {r.source_id for r in rows})
   # ОДИН запрос: SELECT * FROM ontology_fts WHERE kind='chapter' AND source_id IN (?,…)
   # + ОДИН: SELECT id, num_pages FROM sources WHERE id IN (?,…)
   # → {source_id: {"chapters": [(start, end_excl, title)], отсорт. по start}, "num_pages": n}
   # end_excl: page_end (если колонка есть и не NULL; хранится как B → excl=B) иначе next-start иначе num_pages иначе None
for row in rows (в порядке онтологии):
   lo, hi = диапазон по решению 1/2/5
   page, snip = None, None
   for q in (and_q, or_q) (без пустых и без повтора, если or_q == and_q):
       res = _best_page(db, q, sid, lo, hi)   # LIMIT 1, кэш по (q, sid, lo, hi) в пределах вызова
       if res: page, snip = res; break
   if page is not None: hit = SearchHit(page_index=page, snippet=_safe_snippet(snip), precise=True, title=…)
   else: hit = фолбэк (_ontology_row_to_hit-эквивалент, precise=False)
   дедуп (решение 8)
return hits
```
SQL `_best_page`:
```sql
SELECT page_index, snippet(pages_fts, 0, <_SNIP>) AS snip, rank
FROM pages_fts WHERE pages_fts MATCH ? AND source_id = ?
  [AND page_index >= ?] [AND page_index < ?]
ORDER BY rank LIMIT 1
```
Бюджет: 2 вспомогательных запроса + не больше 16 FTS на ≤8 кандидатов. Время этапа логировать
(`log.info("ontology refine: rows=%d fts=%d ms=%.0f")`).

## Декомпозиция (чеклист для worker)

- [x] Шаг 1. Создать `backend/app/snippets.py` и перенести туда `_MARK_OPEN`, `_MARK_CLOSE`,
      `_SNIP`, `_safe_snippet` из `backend/app/routers/search.py`. В `search.py` оставить импорт
      этих имён (`from ..snippets import _MARK_OPEN, _MARK_CLOSE, _SNIP, _safe_snippet`).
      Прогнать существующие тесты: `pytest backend/tests` должен быть зелёным.
- [x] Шаг 2. В `backend/app/schemas.py` добавить в `SearchHit` поле
      `precise: Optional[bool] = None` с комментарием о семантике True/False/None.
- [x] Шаг 3. В `backend/app/ontology.py` реализовать `_chapter_ranges`, `_best_page`,
      `refine_ontology_hits` по алгоритму выше. Константа `_RANGE_MARGIN = 1`. Учесть:
      `page_end` читать только через `"page_end" in row.keys()`; дубликаты стартов у разных
      глав (next-start = строго больший старт); нижняя граница не меньше 0; если `end <= start`,
      то `start + 1`; пустые `and_q`/`or_q` пропускать (если обе пустые, сразу фолбэк без SQL).
      `search_ontology` и `_SQL` не трогать. Обновить docstring модуля (два потребителя ядра +
      этап 2).
- [x] Шаг 4. В `backend/app/routers/chat.py`:
      - `_ontology_row_to_hit` выставляет `precise=False`;
      - `_run_ontology`: `rows = _lookup_ontology(...)`; затем `try: hits = refine_ontology_hits(db, rows, keywords)`
        `except sqlite3.OperationalError: log.warning(...); hits = [_ontology_row_to_hit(o) for o in rows]`;
        импортировать `refine_ontology_hits` в namespace `chat_mod` (для monkeypatch в тестах);
      - обновить docstring модуля и `_run_ontology` (этап 2, фолбэк, без новых LLM).
      `_run_inline` и `_run_tools` не трогать (там онтология только ориентир для модели).
- [x] Шаг 5. Фронт `frontend/src/views/ChatView.vue::hitTitle`: при `h.precise === false` и
      `h.page_index != null` дописывать ` (начало главы)`. Комментарий обновить. Остальное
      без изменений.
- [x] Шаг 6. Загрузчик `ontology/load_annotations.py`:
      - в `parse` извлекать конец регэкспом `стр\.?\s*(\d+)(?:\s*[–—-]\s*(\d+))?` (поиск по всему
        заголовку, как сейчас), возвращать 4-кортеж `(title, page, page_end, summary)`, где
        `page_end = int(B)` или None. Хранится raw B, а exclusive-конец в конвенции `A-1`
        равен B;
      - в `CREATE VIRTUAL TABLE ontology_fts` добавить `page_end UNINDEXED` **в конец** списка
        колонок (индексы колонок для `snippet(ontology_fts, 1, …)` не сдвигаются) и в оба INSERT
        (для book писать NULL);
      - поправить комментарий про конвенцию стр. (смешанная, ±1);
      - запустить `python ontology/load_annotations.py` из корня репо (пишет `data/index.db`;
        бэкенд при этом лучше остановить). Убедиться, что `SELECT COUNT(*) FROM ontology_fts WHERE page_end IS NOT NULL` ≈ 375.
- [x] Шаг 7. Юнит-тесты `backend/tests/test_ontology_refine.py` на **in-memory SQLite** с
      мини-схемой (`pages_fts`, `ontology_fts` с `page_end` и без него, `sources`), чтобы
      проверялось реальное поведение FTS5, а не мок. Кейсы:
      1. в диапазоне главы есть страница с термином, страница вне диапазона с тем же термином
         ранжируется выше → выбрана страница из диапазона, `precise=True`, `<mark>` в сниппете,
         HTML из markdown экранирован (`<img>` превращается в `&lt;img&gt;`);
      2. AND в диапазоне пуст, OR находит → взята OR-страница;
      3. в диапазоне ничего нет → фолбэк: `page_index` = старт, сниппет аннотации, `precise=False`;
      4. `kind=book` → поиск по всему источнику (с нижней границей от первой главы), title =
         глава, в чей диапазон попала страница; при отсутствии совпадений `page_index=None` и
         «о книге»;
      5. глава с `page_index=None` → поиск по всему источнику;
      6. конец главы: при наличии `page_end` используется он, без колонки берётся next-start,
         для последней главы `num_pages`; запас ±1 захватывает страницу `start-1`;
      7. дедуп: две главы (родитель и потомок с перекрытием) дают одну страницу → одна плашка,
         порядок сохранён; book-хит, совпавший со страницей главы, получает title главы;
      8. пустые keywords (`""` или только пунктуация) → без SQL к `pages_fts`, все хиты фолбэк;
      9. число FTS-вызовов: через monkeypatch-счётчик на `_best_page` для 8 строк ≤ 16 и
         кэш на повторяющиеся (q, sid, lo, hi).
- [x] Шаг 8. Обновить `backend/tests/test_chat.py`:
      - в `test_ontology_mode_uses_search_ontology_not_fts` замокать `chat_mod.refine_ontology_hits`
        **или** `app.ontology._best_page` → None. Старые ассерты должны остаться верными, плюс
        `precise is False`. Иначе тест будет зависеть от реальной БД (source_id=3);
      - новый тест: `refine_ontology_hits` бросает `OperationalError` → 200, фолбэк-маппинг;
      - новый тест: refine возвращает точный хит → в ответе `page_index` фрагмента,
        `precise=True`, ровно 1 LLM-вызов, `answer == ""`.
- [x] Шаг 9. Интеграционные тесты в `backend/tests/test_search_real_index.py` (используют
      фикстуру `db` со skip; дополнительно skip, если нет `pages_fts`):
      - `refine_ontology_hits(db, search_ontology(db, "стрептококкоз Streptococcus suis", 8), "стрептококкоз Streptococcus suis")`
        → есть хит с `precise=True`, у которого `page_index` ≠ старт соответствующей строки
        онтологии и лежит в `[start-1, end+1)`; в сниппете есть `<mark>`;
      - число хитов не больше числа строк, пар `(source_id, page_index)` без дублей;
      - `/api/ontology/search?q=Newcastle` → ключи элементов ровно прежние
        (`source_id, source_slug, source_title, chapter_title, page_index, kind, snippet, score`),
        без `page_end`;
      - мягкая проверка времени: refine по 8 строкам < 2 с (только как assert-страховка от N+1).
- [x] Шаг 10. `docs/ARCHITECTURE.md`: в §2 (`mode=ontology`) описать этап 2 (диапазон главы,
      AND→OR в `pages_fts`, фолбэк, `precise`, дедуп, деградация). В «Контракт хита» добавить
      поле `precise`. В разделе «Онтология» сказать, что `ontology_fts` хранит
      `page_index` (старт, A-1) и `page_end` (B), что конвенция страниц в аннотациях смешанная
      (±1), и почему отказались от расширения словарём аннотации. Обновить
      `backend/README.md`, если там описан ответ чата. **TODO.md не трогать.**
- [x] Шаг 11. Прогнать `pytest backend/tests` и `npm run build` во `frontend/`. Вручную (если
      есть LLM-ключ) запросить в чате «стрептококкоз у свиней»: плашки ведут на страницы
      внутри глав, у фолбэков пометка «(начало главы)».
      _Выполнено: pytest 85 passed, npm run build OK. Ручной чат с LLM не делался (бэкенд на 8011 не перезапускался); вместо него — сравнение ДО/ПОСЛЕ прямым вызовом search_ontology + refine_ontology_hits на data/index.db (и на старой копии без page_end)._

## Риски и митигации

| Риск | Митигация |
|---|---|
| Ошибка ±1 в стартах из-за смешанной конвенции A (0/1-based) | Запас `_RANGE_MARGIN=1` с обеих сторон. Сам сдвиг старта не трогаем, это отдельная задача (зафиксировать в ARCHITECTURE) |
| Кривые или отсутствующие концы (одностраничные статьи, вложенные главы, главы вне порядка) | `page_end` из загрузчика; next-start = строго больший старт; `num_pages`; `end<=start` → `start+1`; без старта ищем по всему источнику |
| Вложенные главы с next-start: родитель обрезается до первой подглавы | `page_end` (B из аннотации) решает. Без пересборки родитель фактически становится «вводной частью», это приемлемо, подглавы обычно тоже есть среди кандидатов |
| Главы без страниц («Раздел I») и `kind=book` попадают на оглавление или титул | Нижняя граница = первая известная глава минус margin; подпись главой по диапазону |
| Списки литературы внутри диапазона (латынь плотная) выигрывают BM25 | Принимаем как известное ограничение, задокументировать. Не эвристичить без данных |
| OCR-шум/HTML в сниппете → XSS через `v-html` | Только `_safe_snippet` (escape + плейсхолдеры private-use), тест на `<img>` |
| OR-ступень даёт слабый фрагмент (совпало одно слово) | Диапазон уже тематически отфильтрован онтологией; AND всегда первым |
| Производительность (префиксные MATCH по всему корпусу, затем фильтр по UNINDEXED) | ≤2+16 запросов, `LIMIT 1`, кэш в пределах вызова, лог времени, мягкий тест < 2 с. Если будет медленно, следующий шаг — один MATCH с `source_id IN (...)` и раскладка в Python |
| Старый `index.db` (без `ontology_fts`/`page_end`) | `row.keys()` для `page_end`; `OperationalError` → старый маппинг |
| Существующий тест чата зависит от реальной БД после изменения | Шаг 8: мокать refine/`_best_page` |
| Пересборка `index.db` загрузчиком при запущенном бэкенде | Бэкенд на `mode=ro`, но скрипт запускать при остановленном бэкенде; загрузчик идемпотентен |
| Внешний скилл `/api/ontology/search` | `_SQL` не трогаем, тест на неизменность ключей |

## Затронутые файлы

- `E:\Pets\vet-DB\backend\app\snippets.py` (новый)
- `E:\Pets\vet-DB\backend\app\routers\search.py` (импорт хелперов сниппета)
- `E:\Pets\vet-DB\backend\app\schemas.py` (`SearchHit.precise`)
- `E:\Pets\vet-DB\backend\app\ontology.py` (`refine_ontology_hits`, `_chapter_ranges`, `_best_page`)
- `E:\Pets\vet-DB\backend\app\routers\chat.py` (`_run_ontology`, `_ontology_row_to_hit`, docstrings)
- `E:\Pets\vet-DB\ontology\load_annotations.py` (`page_end`) + пересборка `data\index.db` скриптом
- `E:\Pets\vet-DB\frontend\src\views\ChatView.vue` (`hitTitle`)
- `E:\Pets\vet-DB\backend\tests\test_ontology_refine.py` (новый)
- `E:\Pets\vet-DB\backend\tests\test_chat.py`
- `E:\Pets\vet-DB\backend\tests\test_search_real_index.py`
- `E:\Pets\vet-DB\docs\ARCHITECTURE.md`, при необходимости `E:\Pets\vet-DB\backend\README.md`
- НЕ трогать: `TODO.md`, `backend/app/routers/ontology.py`, `_SQL` в `ontology.py`

## Доработка по ревью (итерация 2)

- [x] Критичное: `_best_page` берёт до `_CANDIDATES=60` кандидатов одним запросом и пропускает страницы списка литературы (`_is_bibliography`, классификатор ревьюера). Если в ступени только литература, ступень пустая: дальше OR, затем фолбэк. Тесты: юнит-тесты детектора, выбор обычной страницы, фолбэк при «только литературе», регрессия Newcastle на реальном индексе.
- [x] Предупреждение: в `_chapter_ranges` колонки перечислены явно, наличие `page_end` проверяется через `PRAGMA table_info(ontology_fts)`.
- [x] Предупреждение: фолбэк сдвигается на +1, если старт главы — литература, а следующая страница нет (`_shift_fallback_start`, один точечный запрос).
- [x] Рекомендации: публичные `fallback_hit`, `safe_snippet`, `SNIPPET_ARGS` с алиасами старых имён; в логе добавлены `fallbacks` и `bib_skipped`.
