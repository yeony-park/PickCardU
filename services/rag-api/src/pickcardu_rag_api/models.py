from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pickcardu_rag import AtomicClaim, Recommendation


ProfileName = Literal["card_page_section_benefit", "parent_child_bundle"]
QueryType = Literal["proper_noun", "numeric_condition", "semantic"]


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    profile: ProfileName | None = None
    top_k: Literal[1, 3, 5] = 3

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class ErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool
    request_id: str


class LiveResponse(BaseModel):
    status: Literal["live"]


class ReadyResponse(BaseModel):
    status: Literal["ready"]
    release_id: str
    profile: ProfileName
    document_count: int
    chunk_count: int


class NotReadyResponse(BaseModel):
    status: Literal["not_ready"]
    reason: str


class CardResult(BaseModel):
    card_key: str
    card_name: str
    issuer: str
    score: float
    rank: int
    evidence_count: int


class EvidenceResult(BaseModel):
    rank: int
    card_key: str
    card_name: str
    issuer: str
    chunk_id: str
    page_num: int
    text: str
    section: str | None
    level: str
    score: float


class SearchUsage(BaseModel):
    embedding: dict[str, Any]


class AnswerUsage(SearchUsage):
    answer: dict[str, Any]


class SearchResponse(BaseModel):
    status: Literal["completed"]
    release_id: str
    profile: ProfileName
    query_type: QueryType
    cards: list[CardResult]
    evidence: list[EvidenceResult]
    usage: SearchUsage


class AnswerResponse(BaseModel):
    status: Literal["completed"]
    answer_status: Literal["answered", "insufficient_evidence"]
    release_id: str
    profile: ProfileName
    query_type: QueryType
    cards: list[CardResult]
    answer: str
    recommendations: list[Recommendation]
    claims: list[AtomicClaim]
    evidence: list[EvidenceResult]
    usage: AnswerUsage
