"""Pydantic-модели ответов API (контракт SPEC §4)."""
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class SourceListItem(BaseModel):
    id: int
    slug: Optional[str] = None
    title: Optional[str] = None
    num_pages: Optional[int] = None
    has_pdf: bool = False
    source_url: Optional[str] = None


class SourceDetail(BaseModel):
    id: int
    title: Optional[str] = None
    num_pages: Optional[int] = None
    has_pdf: bool = False
    pdf_url: Optional[str] = None
    source_url: Optional[str] = None
    description: Optional[str] = None


class TocItem(BaseModel):
    title: Optional[str] = None
    level: Optional[int] = None
    page_index: Optional[int] = None
    origin: Optional[str] = "markdown"


class PageDetail(BaseModel):
    source_id: int
    page_index: int
    markdown: str
    num_pages: Optional[int] = None


class SearchHit(BaseModel):
    type: Literal["page", "preparation", "disease"]
    # для type == page
    source_id: Optional[int] = None
    source_title: Optional[str] = None
    page_index: Optional[int] = None
    # для preparation / disease
    ref_id: Optional[int] = None
    title: Optional[str] = None
    # общее
    snippet: Optional[str] = None
    score: Optional[float] = None


# --- Чат «найти источник» -------------------------------------------------

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: Optional[List[ChatMessage]] = None
    # Необязательный тред: если не задан/не найден — создаётся новая беседа.
    conversation_id: Optional[int] = None


class ChatUsage(BaseModel):
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tool_calls: int = 0


class ChatResponse(BaseModel):
    answer: str
    hits: List[SearchHit] = []
    usage: ChatUsage = ChatUsage()
    # id беседы (созданной или продолженной) — для привязки истории на клиенте.
    conversation_id: int


# --- История чатов --------------------------------------------------------

class ConversationListItem(BaseModel):
    id: int
    title: Optional[str] = None
    created_at: str
    updated_at: str
    message_count: int = 0


class ConversationMessage(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    hits: List[SearchHit] = []
    usage: Optional[ChatUsage] = None
    created_at: str


class ConversationDetail(BaseModel):
    id: int
    title: Optional[str] = None
    created_at: str
    updated_at: str
    messages: List[ConversationMessage] = []


class PreparationListItem(BaseModel):
    id: int
    origin: Optional[str] = None
    trade_name: Optional[str] = None
    generic_name: Optional[str] = None
    drug_class: Optional[str] = None
    target_animals: Optional[str] = None
    manufacturer: Optional[str] = None


class PreparationDetail(BaseModel):
    id: int
    origin: Optional[str] = None
    trade_name: Optional[str] = None
    generic_name: Optional[str] = None
    drug_class: Optional[str] = None
    dosage_form: Optional[str] = None
    route: Optional[str] = None
    target_animals: Optional[str] = None
    manufacturer: Optional[str] = None
    reg_number: Optional[str] = None
    instruction_md: Optional[str] = None


class DiseaseListItem(BaseModel):
    id: int
    slug: Optional[str] = None
    species: Optional[str] = None
    name: Optional[str] = None


class DiseaseDetail(BaseModel):
    id: int
    slug: Optional[str] = None
    species: Optional[str] = None
    name: Optional[str] = None
    data: Any = None
