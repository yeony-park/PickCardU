from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .models import AnswerResponse, ErrorResponse, QueryRequest


class BrowserSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BrowserSessionResponse(BaseModel):
    status: Literal['ready']


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_conversation_id: UUID


class Conversation(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationPage(BaseModel):
    conversations: list[Conversation]
    next_cursor: str | None


class ChatRequest(QueryRequest):
    client_request_id: UUID
    retry_failed: bool = False


class ChatRewriteUsage(BaseModel):
    provider_called: bool
    model: str | None = None
    latency_ms: float | None = None
    usage: dict[str, Any] | None = None


class ChatMessage(BaseModel):
    id: str
    turn_id: str
    client_request_id: str
    seq: int
    role: Literal['user', 'assistant']
    content: str
    status: Literal['completed', 'pending', 'failed']
    answer: AnswerResponse | None = None
    rewrite_usage: ChatRewriteUsage | None = None
    error: ErrorResponse | None = None
    created_at: str


class TurnResponse(BaseModel):
    turn_id: str
    messages: list[ChatMessage]


class MessagesPage(BaseModel):
    messages: list[ChatMessage]
    next_before_seq: int | None
    has_pending: bool
