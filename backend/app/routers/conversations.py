"""История чатов: список бесед, сообщения одной беседы, полная очистка.

Единственная разрушающая операция — `POST /clear` (очистить всю историю). Добавлена
по явному запросу: в UI это кнопка с иконкой + модальное подтверждение. История
общая на всех (авторизации нет), поэтому очистка стирает историю для всех — это
осознанное поведение внутреннего инструмента. См. backend/README.md.
"""
from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query

from .. import history
from ..schemas import (
    ChatUsage,
    ConversationDetail,
    ConversationListItem,
    ConversationMessage,
    SearchHit,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

log = logging.getLogger("vetai.conversations")


def _loads(raw: Optional[str]) -> Any:
    """Безопасная десериализация JSON из БД (json.loads, без eval)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


@router.get("", response_model=List[ConversationListItem])
def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> List[ConversationListItem]:
    rows = history.list_conversations(limit=limit, offset=offset)
    return [ConversationListItem(**r) for r in rows]


@router.post("/clear")
def clear_conversations() -> dict:
    """Полностью очистить историю чатов (все беседы). Вызывается из UI по кнопке
    с подтверждением. История общая → очищает для всех пользователей."""
    n = history.clear_all()
    log.info("history cleared: %d conversations removed", n)
    return {"cleared": n}


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int) -> ConversationDetail:
    conv = history.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена.")
    rows = history.get_conversation_messages(conversation_id)
    messages: List[ConversationMessage] = []
    for r in rows:
        hits_data = _loads(r.get("hits_json")) or []
        usage_data = _loads(r.get("usage_json"))
        hits = [SearchHit(**h) for h in hits_data if isinstance(h, dict)]
        usage = ChatUsage(**usage_data) if isinstance(usage_data, dict) else None
        messages.append(
            ConversationMessage(
                id=r["id"],
                role=r["role"],
                content=r["content"],
                hits=hits,
                usage=usage,
                created_at=r["created_at"],
            )
        )
    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"],
        updated_at=conv["updated_at"],
        messages=messages,
    )
