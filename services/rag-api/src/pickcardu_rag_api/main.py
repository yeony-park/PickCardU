from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pickcardu_rag import (
    CHUNKING_PROFILES,
    AtomicClaim,
    AnswerOutput,
    LocalReranker,
    OpenAIService,
    RagError,
    Recommendation,
    SearchConfig,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings, load_settings, validate_settings
from .index import ActiveIndexLoader, ReleaseHandle


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


ERROR_RESPONSES = {
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


def _error(status: int, code: str, message: str, request_id: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(code=code, message=message, retryable=retryable, request_id=request_id).model_dump(),
    )


def _search(
    payload: QueryRequest,
    loader: Any,
    provider: Any,
) -> tuple[ReleaseHandle, dict[str, Any], dict[str, Any]]:
    handle = loader.load()
    profile = payload.profile or handle.manifest["strategy"]
    if profile != handle.manifest["strategy"] or profile not in CHUNKING_PROFILES:
        raise ValueError("requested profile does not match the active index")
    if getattr(provider, "embedding_model", None) != handle.manifest["embedding_model"]:
        raise ValueError("runtime embedding model does not match the active index")
    vector, embedding_usage = provider.embed(payload.query)
    result = handle.search(
        payload.query,
        vector,
        SearchConfig(
            profile=profile,
            vector_weight=0.4,
            component_depth=50,
            candidate_depth=20,
            top_k=payload.top_k,
            reranker="bge",
            reranker_route="all" if profile == "parent_child_bundle" else "selective",
        ),
    )
    return handle, result, embedding_usage


def create_app(
    settings: Settings | None = None,
    *,
    provider: Any = None,
    index_loader: Any = None,
    reranker: Any = None,
) -> FastAPI:
    settings = validate_settings(settings or load_settings())
    provider = provider or OpenAIService(
        api_key=settings.openai_api_key,
        embedding_model=settings.embedding_model,
        llm_model=settings.llm_model,
    )
    reranker = reranker or LocalReranker(str(settings.bge_model_path))
    loader = index_loader or ActiveIndexLoader(settings.index_runtime_root, reranker=reranker)
    app = FastAPI(title="PickCardU RAG API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _error_value: RequestValidationError):
        return _error(422, "INVALID_REQUEST", "요청 형식이 올바르지 않습니다.", request.state.request_id)

    @app.exception_handler(RagError)
    async def rag_error(request: Request, error: RagError):
        return _error(503, error.code, error.message, request.state.request_id, retryable=error.retryable)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, error: ValueError):
        return _error(409, "CONTRACT_MISMATCH", str(error), request.state.request_id)

    @app.exception_handler(RuntimeError)
    async def runtime_error(request: Request, _error_value: RuntimeError):
        return _error(
            503,
            "INDEX_UNAVAILABLE",
            "활성 검색 인덱스를 사용할 수 없습니다.",
            request.state.request_id,
            retryable=True,
        )

    @app.get("/v1/health/live", response_model=LiveResponse)
    def live() -> LiveResponse:
        return LiveResponse(status="live")

    @app.get(
        "/v1/health/ready",
        response_model=ReadyResponse,
        responses={503: {"model": NotReadyResponse}},
    )
    def ready() -> Any:
        try:
            handle = loader.load()
        except Exception as error:
            return JSONResponse(status_code=503, content={"status": "not_ready", "reason": type(error).__name__})
        return {
            "status": "ready",
            "release_id": handle.release_id,
            "profile": handle.manifest["strategy"],
            "document_count": len(handle.manifest["document_ids"]),
            "chunk_count": len(handle.manifest["chunk_ids"]),
        }

    @app.post("/v1/search", response_model=SearchResponse, responses=ERROR_RESPONSES)
    def search(payload: QueryRequest) -> SearchResponse:
        handle, result, embedding_usage = _search(payload, loader, provider)
        return SearchResponse.model_validate({
            "status": "completed",
            "release_id": handle.release_id,
            "profile": handle.manifest["strategy"],
            "query_type": result["query_type"],
            "cards": result["cards"],
            "evidence": result["evidence"],
            "usage": {"embedding": embedding_usage},
        })

    @app.post("/v1/answer", response_model=AnswerResponse, responses=ERROR_RESPONSES)
    def answer(payload: QueryRequest) -> AnswerResponse:
        handle, result, embedding_usage = _search(payload, loader, provider)
        if result["evidence"]:
            generated, answer_usage = provider.answer(payload.query, result["evidence"])
        else:
            generated = AnswerOutput(
                answer_status="insufficient_evidence",
                answer_text="현재 등록된 카드 문서에서는 질문을 뒷받침할 근거를 확인하기 어렵습니다.",
            )
            answer_usage = {"provider_called": False}
        visible_cards = [] if generated.answer_status == "insufficient_evidence" else result["cards"]
        visible_evidence = [] if generated.answer_status == "insufficient_evidence" else result["evidence"]
        return AnswerResponse.model_validate({
            "status": "completed",
            "answer_status": generated.answer_status,
            "release_id": handle.release_id,
            "profile": handle.manifest["strategy"],
            "query_type": result["query_type"],
            "cards": visible_cards,
            "answer": generated.answer_text,
            "recommendations": [item.model_dump() for item in generated.recommendations],
            "claims": [item.model_dump() for item in generated.claims],
            "evidence": visible_evidence,
            "usage": {"embedding": embedding_usage, "answer": answer_usage},
        })

    return app


app = create_app()
