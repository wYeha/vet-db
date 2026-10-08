"""FastAPI-приложение VetAI: REST поверх index.db (SPEC §4)."""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config, history
from .routers import chat, conversations, diseases, ontology, preparations, search, sources

# Инициализация writable-БД истории чатов (создаёт data/ и схему при отсутствии).
# index.db не затрагивается — это отдельный файл (см. history.py).
history.init_schema()

app = FastAPI(title="VetAI Knowledge Base API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sources.router)
app.include_router(search.router)
app.include_router(preparations.router)
app.include_router(diseases.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(ontology.router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "db_path": config.DB_PATH,
        "db_exists": os.path.exists(config.DB_PATH),
        "history_db_path": config.HISTORY_DB_PATH,
        "history_db_exists": os.path.exists(config.HISTORY_DB_PATH),
        "s3_configured": bool(config.S3_ACCESS_KEY and config.S3_SECRET_KEY),
        "llm_configured": config.LLM_CONFIGURED,
    }
