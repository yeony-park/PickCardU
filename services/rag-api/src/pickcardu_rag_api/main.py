from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pickcardu_rag import (
    CHUNKING_PROFILES,
    AnswerOutput,
    LocalReranker,
    OpenAIService,
    RagError,
    SearchConfig,
    answer_payload_limit,
)

from .config import Settings, load_settings, validate_settings
from .index import ActiveIndexLoader, ReleaseHandle


from .models import (
    AnswerResponse, ErrorResponse, LiveResponse, NotReadyResponse, QueryRequest,
    ReadyResponse, SearchResponse,
)


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
    *,
    target_card_keys: tuple[str, ...] | None = None,
    answer_payload_bytes: int | None = None,
) -> tuple[ReleaseHandle, dict[str, Any], dict[str, Any]]:
    handle = loader.load()
    profile = payload.profile or handle.manifest["strategy"]
    if profile != handle.manifest["strategy"] or profile not in CHUNKING_PROFILES:
        raise ValueError("requested profile does not match the active index")
    if getattr(provider, "embedding_model", None) != handle.manifest["embedding_model"]:
        raise ValueError("runtime embedding model does not match the active index")
    if target_card_keys is not None:
        unavailable = [key for key in target_card_keys if key not in handle.manifest['document_ids']]
        if unavailable:
            return handle, {'query_type': 'semantic', 'cards': [], 'evidence': [],
                            'trace': {'unavailable_card_keys': unavailable}}, {'provider_called': False}
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
            target_card_keys=target_card_keys,
            answer_payload_bytes=answer_payload_limit() if answer_payload_bytes is None else answer_payload_bytes,
        ),
    )
    return handle, result, embedding_usage


def create_app(
    settings: Settings | None = None,
    *,
    provider: Any = None,
    index_loader: Any = None,
    reranker: Any = None,
    chat_store: Any = None,
) -> FastAPI:
    settings = validate_settings(settings or load_settings())
    provider = provider or OpenAIService(
        api_key=settings.openai_api_key,
        embedding_model=settings.embedding_model,
        llm_model=settings.llm_model,
        answer_payload_bytes=settings.answer_payload_bytes,
    )
    if getattr(provider, 'answer_payload_bytes', settings.answer_payload_bytes) != settings.answer_payload_bytes:
        raise ValueError('provider and search answer payload byte limits must match')
    reranker = reranker or LocalReranker(str(settings.bge_model_path))
    loader = index_loader or ActiveIndexLoader(settings.index_runtime_root, reranker=reranker)
    app = FastAPI(title="PickCardU RAG API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        if request.url.path == '/v1/browser-session' or request.url.path.startswith('/v1/conversations'):
            from .chat import browser_token, refresh_cookie
            response.headers['cache-control'] = 'no-store'
            token = browser_token(request)
            if token:
                refresh_cookie(response, token)
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
        handle, result, embedding_usage = _search(payload, loader, provider, answer_payload_bytes=settings.answer_payload_bytes)
        return SearchResponse.model_validate({
            "status": "completed",
            "release_id": handle.release_id,
            "profile": handle.manifest["strategy"],
            "query_type": result["query_type"],
            "cards": result["cards"],
            "evidence": result["evidence"],
            "usage": {"embedding": embedding_usage},
        })

    def _generate_answer(payload: QueryRequest, *, target_card_keys: tuple[str, ...] | None = None,
                         clarification: str | None = None) -> AnswerResponse:
        if clarification:
            handle = loader.load()
            if payload.profile is not None and payload.profile != handle.manifest['strategy']:
                raise ValueError('requested profile does not match the active index')
            result = {'query_type': 'semantic', 'cards': [], 'evidence': [], 'trace': {}}
            embedding_usage = {'provider_called': False}
        else:
            handle, result, embedding_usage = _search(payload, loader, provider, target_card_keys=target_card_keys,
                                                     answer_payload_bytes=settings.answer_payload_bytes)
        counts = {key: sum(e['card_key'] == key for e in result['evidence']) for key in target_card_keys or ()}
        missing = [key for key, count in counts.items() if count == 0]
        if clarification:
            generated = AnswerOutput(answer_status='insufficient_evidence', answer_text=clarification)
            answer_usage = {'provider_called': False, 'reason': 'clarification_required'}
        elif missing:
            catalog = {card['card_key']: card['card_name'] for card in handle.catalog}
            names = ', '.join(catalog.get(key, '이전 추천 카드') for key in missing)
            generated = AnswerOutput(answer_status='insufficient_evidence',
                answer_text=f'{names}의 질문 관련 근거를 확보하지 못해 요청하신 카드 전체를 비교하기 어렵습니다. 조건이 없다는 뜻은 아닙니다.')
            answer_usage = {'provider_called': False, 'reason': 'missing_target_evidence'}
        elif result["evidence"]:
            if target_card_keys is None:
                generated, answer_usage = provider.answer(payload.query, result["evidence"])
            else:
                generated, answer_usage = provider.answer(payload.query, result["evidence"], comparison=True)
        else:
            generated = AnswerOutput(
                answer_status="insufficient_evidence",
                answer_text="현재 등록된 카드 문서에서는 질문을 뒷받침할 근거를 확인하기 어렵습니다.",
            )
            answer_usage = {"provider_called": False}
        trace = result.get('trace', {})
        chunks = {chunk.chunk_id: chunk.card_key for chunk in handle.chunks}
        retrieved_ids = {row['chunk_id'] for name in ('bm25', 'vector') for row in trace.get(name, [])}
        answer_usage = {**answer_usage, 'retrieval': {
            'target_card_keys': list(target_card_keys) if target_card_keys is not None else None,
            'retrieved_card_keys': sorted({chunks[key] for key in retrieved_ids if key in chunks}),
            'evidence_card_keys': list(dict.fromkeys(e['card_key'] for e in result['evidence'])),
            'evidence_counts': counts,
            'missing_card_keys': missing,
            'unavailable_card_keys': trace.get('unavailable_card_keys', []),
            'evidence_budget': trace.get('evidence_budget'),
            'stages': {name: [row['chunk_id'] for row in trace.get(name, [])] for name in ('bm25', 'vector', 'rrf', 'leaf', 'rerank')},
        }}
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

    def generate_answer(payload: QueryRequest) -> AnswerResponse:
        return _generate_answer(payload)

    app.post('/v1/answer', name='answer', response_model=AnswerResponse, responses=ERROR_RESPONSES)(generate_answer)

    from .chat import register_chat_routes
    from .chat_store import ChatStore, ChatStoreError

    @app.exception_handler(ChatStoreError)
    async def chat_error(request: Request, error: ChatStoreError):
        return _error(error.status_code, error.code, error.message, request.state.request_id, retryable=error.retryable)

    path = settings.chat_db_path or Path(__file__).resolve().parents[4] / 'data/chat/runtime/chat.sqlite'
    register_chat_routes(app, settings, chat_store if chat_store is not None else ChatStore(path), provider, _generate_answer)
    return app


app = create_app()
