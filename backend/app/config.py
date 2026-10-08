"""Конфигурация backend: всё читается из переменных окружения.

Секреты (S3 ключи) в коде не хранятся. Для локальной разработки можно положить
их в `.env` в корне репозитория (см. `.env.example`) — при наличии python-dotenv
он подхватится автоматически, иначе задавать через окружение.
"""
import os

# Необязательная загрузка .env (dev-удобство). В проде — реальные env.
try:  # pragma: no cover
    from dotenv import load_dotenv

    _here = os.path.dirname(os.path.abspath(__file__))
    for _candidate in (
        os.path.join(_here, "..", "..", ".env"),  # корень репозитория
        os.path.join(_here, "..", ".env"),        # backend/.env
    ):
        if os.path.exists(_candidate):
            load_dotenv(_candidate)
            break
except Exception:  # dotenv не установлен — не критично
    pass


def _default_db_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "data", "index.db"))


def _default_history_db_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "data", "history.db"))


# Путь к индексу (read-only)
DB_PATH = os.environ.get("DB_PATH", _default_db_path())

# Путь к writable-базе истории чатов (отдельный файл, НЕ index.db; см. history.py)
HISTORY_DB_PATH = os.environ.get("HISTORY_DB_PATH", _default_history_db_path())

# S3 (для presigned PDF)
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "https://s3.twcstorage.ru")
S3_BUCKET = os.environ.get("S3_BUCKET", "lz810806-ai-data")
S3_REGION = os.environ.get("S3_REGION", "ru-1")
S3_ACCESS_KEY = os.environ.get("AK") or os.environ.get("S3_ACCESS_KEY")
S3_SECRET_KEY = os.environ.get("SK") or os.environ.get("S3_SECRET_KEY")

# TTL presigned-ссылки на PDF, секунды
PDF_URL_TTL = int(os.environ.get("PDF_URL_TTL", "300"))

# CORS origins (через запятую). Дефолт — типовые dev-порты Vite.
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]

# --- LLM (чат-ассистент «найти источник») --------------------------------
# Всё за env; ключ/URL/модель — секреты, в коде значений нет. Пока не заданы —
# чат отдаёт 503 (см. routers/chat.py). router.ai — OpenAI-совместимый API.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "30"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "700"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.1"))
# Максимум итераций function-calling цикла (защита от зацикливания retrieval).
LLM_MAX_TOOL_CALLS = int(os.environ.get("LLM_MAX_TOOL_CALLS", "3"))
# inline — извлечение ключевых слов → FTS → ответ (основной, надёжный режим);
# tools — function calling (нестабилен у части моделей, оставлен как опция).
LLM_TOOL_MODE = os.environ.get("LLM_TOOL_MODE", "inline")
# Суточный бюджет в рублях (грубый гард по числу запросов/токенов).
LLM_DAILY_BUDGET_RUB = float(os.environ.get("LLM_DAILY_BUDGET_RUB", "100"))
# Грубая оценка стоимости 1К токенов (руб.) — для суточного гарда.
LLM_RUB_PER_1K_TOKENS = float(os.environ.get("LLM_RUB_PER_1K_TOKENS", "0.2"))
# Подмешивать ли карту онтологии (смысловые аннотации книг/глав) в inline-чат.
# Локальный SQL по уже открытому read-only соединению, без новых LLM-вызовов.
LLM_USE_ONTOLOGY = os.environ.get("LLM_USE_ONTOLOGY", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
    "",
)
# Отключать ли «рассуждения» (reasoning) у модели. deepseek-*-flash — reasoning-
# модель: без этого весь бюджет max_tokens уходит в reasoning-токены, а content
# приходит пустым (finish_reason=length) → чат сваливается в generic-заглушку.
# Для навигации по онтологии chain-of-thought не нужен: с reasoning=off ответ
# приходит сразу и запрос дешевле (~в 10 раз). Передаётся как {"reasoning":
# {"enabled": false}} (OpenRouter-совместимо; RouterAI это поддерживает).
LLM_DISABLE_REASONING = os.environ.get("LLM_DISABLE_REASONING", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
    "",
)

# Ассистент считается сконфигурированным только при наличии ключа, URL и модели.
LLM_CONFIGURED = bool(LLM_API_KEY and LLM_BASE_URL and LLM_MODEL)
