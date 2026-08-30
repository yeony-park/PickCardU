import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import Cookie, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pickcardu_rag import (
    EMBEDDING_MODEL_CONTRACT,
    LLM_MODEL_CONTRACT,
    EmbeddingUnavailable,
    IndexIdentity,
    LlmUnavailable,
    LocalReranker,
    OpenAIService,
    RagError,
    RerankerUnavailable,
    SearchConfig,
    comparison_config,
    completed_context,
    retrieval_metrics,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import storage
from .config import Settings, load_settings, validate_settings
from .errors import ApiError, IndexUnavailable
from .index import ActiveIndexLoader, ReleaseHandle
from .security import AccountLocks, LoginRateLimiter


SESSION_COOKIE = "pickcardu_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
AGE_BANDS = {"under_20", "20s", "30s", "40s", "50s", "60_plus"}
BENEFIT_CATEGORIES = {"transportation", "shopping", "dining", "cafe", "travel", "airline", "online", "utilities", "telecom", "fuel", "medical", "education", "other"}
MONTHLY_SPEND_BANDS = {"under_500k", "500k_1m", "1m_2m", "2m_plus"}
ANNUAL_FEE_PREFERENCES = {"low", "balanced", "no_preference"}
CARD_TYPE_PREFERENCES = {"credit", "check", "no_preference"}


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1000)


class UserResponse(BaseModel):
    id: str
    username: str
    role: Literal["user", "developer"]
    active: int
    onboarding_skipped_at: str | None
    created_at: str
    updated_at: str


class AuthResponse(BaseModel):
    user: UserResponse
    profile_state: Literal["required", "complete", "skipped"]


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=30)
    age_band: str | None = None
    benefit_categories: list[str] | None = Field(default=None, max_length=12)
    monthly_spend_band: str | None = None
    annual_fee_preference: str | None = None
    card_type_preference: str | None = None
    owned_card_ids: list[str] | None = Field(default=None, max_length=10)

    @field_validator("age_band")
    @classmethod
    def age(cls, value: str | None) -> str | None:
        if value is not None and value not in AGE_BANDS:
            raise ValueError("unsupported age band")
        return value

    @field_validator("benefit_categories")
    @classmethod
    def categories(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (len(value) != len(set(value)) or any(item not in BENEFIT_CATEGORIES for item in value)):
            raise ValueError("unsupported benefit category")
        return value

    @field_validator("monthly_spend_band")
    @classmethod
    def spend(cls, value: str | None) -> str | None:
        if value is not None and value not in MONTHLY_SPEND_BANDS:
            raise ValueError("unsupported spend band")
        return value

    @field_validator("annual_fee_preference")
    @classmethod
    def fee(cls, value: str | None) -> str | None:
        if value is not None and value not in ANNUAL_FEE_PREFERENCES:
            raise ValueError("unsupported fee preference")
        return value

    @field_validator("card_type_preference")
    @classmethod
    def card_type(cls, value: str | None) -> str | None:
        if value is not None and value not in CARD_TYPE_PREFERENCES:
            raise ValueError("unsupported card type")
        return value


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=100)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        if not (value := value.strip()):
            raise ValueError("query must not be blank")
        return value


class LabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["benefit_hierarchy"] = "benefit_hierarchy"
    vector_weight: Literal[0.0, 0.2, 0.4, 0.6, 0.8, 1.0] = 0.4
    component_depth: Literal[50] = 50
    candidate_depth: Literal[10, 20, 50] = 20
    top_k: Literal[1, 3, 5] = 3
    reranker: Literal["off", "bge", "gte"] = "bge"
    reranker_route: Literal["selective", "all"] = "selective"
    include_llm: bool = False
    llm_model: Literal["gpt-5.6-luna"] = "gpt-5.6-luna"

    def search_config(self) -> SearchConfig:
        return SearchConfig(**self.model_dump(exclude={"include_llm", "llm_model"}))


class LabRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    config: LabConfig = Field(default_factory=LabConfig)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        if not (value := value.strip()):
            raise ValueError("query must not be blank")
        return value


class CatalogCard(BaseModel):
    card_key: str
    card_name: str
    issuer: str


class CatalogResponse(BaseModel):
    cards: list[CatalogCard]
    metadata: dict[str, Any]


class ProfileResponse(BaseModel):
    profile: dict[str, Any] | None
    profile_state: Literal["required", "complete", "skipped"]


class ConversationRecord(BaseModel):
    id: str
    user_id: str
    title: str | None
    created_at: str
    updated_at: str


class ConversationResponse(BaseModel):
    conversation: ConversationRecord


class ConversationListResponse(BaseModel):
    conversations: list[ConversationRecord]


class MessageRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    conversation_id: str
    user_id: str
    role: Literal["user", "assistant"]
    content: str
    metadata: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    created_at: str


class ConversationDetailResponse(BaseModel):
    conversation: ConversationRecord
    messages: list[MessageRecord]


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


class RetrievalPreview(BaseModel):
    run_id: str
    status: Literal["retrieval_only"]
    label: str
    cards: list[CardResult]
    evidence: list[EvidenceResult]
    query_type: str
    rewrite_status: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    retryable: bool
    request_id: str
    run_id: str | None = None


class ChatFailureResponse(ErrorResponse):
    retrieval_preview: RetrievalPreview | None = None


class RecommendationResult(BaseModel):
    card_key: str
    reason: str
    citations: list[str]


class ClaimResult(BaseModel):
    card_key: str
    text: str
    value: str | float | int | None
    unit: str | None
    conditions: list[str]
    citations: list[str]


class ChatResponse(BaseModel):
    run_id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    status: Literal["completed"]
    rewrite_status: str
    cards: list[CardResult]
    answer: str
    recommendations: list[RecommendationResult]
    claims: list[ClaimResult]
    evidence: list[EvidenceResult]
    query_type: str
    citations_semantically_verified: Literal[False]
    metadata: dict[str, Any]


def _error_responses(*statuses: int, chat_failure: bool = False) -> dict[int, dict[str, type[BaseModel]]]:
    return {status: {"model": ChatFailureResponse if chat_failure and status == 503 else ErrorResponse} for status in statuses}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _profile_state(connection: sqlite3.Connection, user: dict[str, Any]) -> str:
    if storage.get_profile(connection, user["id"]) is not None:
        return "complete"
    return "skipped" if user["onboarding_skipped_at"] else "required"


def _metadata(handle: ReleaseHandle | None) -> dict[str, Any]:
    return {
        "corpus": {
            "card_count": len(handle.catalog) if handle else None,
            "release_id": handle.release_id if handle else None,
            "created_at": handle.manifest.get("created_at") if handle else None,
            "display": "현재 활성 인덱스 지원 카드 테스트 추천",
        },
        "notice": "추천 전 공식 상품설명서를 다시 확인하세요.",
        "public_operation_blocked": True,
        "public_operation_message": "공개 운영 승인이 완료되지 않은 테스트 서비스입니다.",
    }


def _preview(run_id: str, result: dict[str, Any], rewrite_status: str) -> dict[str, Any]:
    return {"run_id": run_id, "status": "retrieval_only", "label": "검색 진단 미리보기 · 추천 완료 아님", "cards": result["cards"], "evidence": result["evidence"], "query_type": result["query_type"], "rewrite_status": rewrite_status}


def create_app(settings: Settings | None = None, *, provider: Any = None, index_loader: Any = None, reranker: Any = None) -> FastAPI:
    # ponytail: internal MVP runs blocking local/provider calls inline; use a bounded executor before multi-account scale.
    runtime = validate_settings(settings) if settings else load_settings()
    provider_injected = provider is not None
    provider = provider or OpenAIService(api_key=runtime.openai_api_key, embedding_model=runtime.embedding_model, llm_model=runtime.llm_model)
    reranker = reranker or LocalReranker(str(runtime.bge_model_path), str(runtime.gte_model_path) if runtime.gte_model_path else None, runtime.gte_allow_custom_code)
    loader = index_loader or ActiveIndexLoader(runtime.index_runtime_root, reranker=reranker)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        connection = storage.connect(runtime.database_path)
        try:
            storage.init(connection)
            seed_environment = dict(os.environ)
            seed_environment["PICKCARDU_ENV"] = runtime.environment
            storage.seed_from_env(connection, seed_environment)
            app.state.dummy_password = storage.hash_password("dummy login password")
        finally:
            connection.close()
        yield

    app = FastAPI(title="PickCardU RAG API", version="1.0.0", lifespan=lifespan)
    app.state.settings, app.state.provider, app.state.index_loader = runtime, provider, loader
    app.state.reranker, app.state.rate_limiter, app.state.account_locks = reranker, LoginRateLimiter(), AccountLocks()
    app.state.dummy_password = None
    app.add_middleware(CORSMiddleware, allow_origins=list(runtime.allowed_origins), allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def request_guard(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("Origin")
            missing_allowed = runtime.allow_missing_origin_for_tests and origin is None
            if not missing_allowed and (origin is None or origin not in runtime.allowed_origins):
                return JSONResponse(status_code=403, content={"code": "ORIGIN_FORBIDDEN", "message": "허용되지 않은 Origin입니다.", "retryable": False, "request_id": request.state.request_id})
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        fields = {"code": exc.code, "message": exc.message, "retryable": exc.retryable, "request_id": request.state.request_id, "run_id": exc.run_id}
        content = ChatFailureResponse(**fields, retrieval_preview=exc.retrieval_preview).model_dump(mode="json") if exc.retrieval_preview is not None else ErrorResponse(**fields).model_dump(mode="json")
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        code = "RUN_CONFIG_INVALID" if request.url.path.startswith("/internal/v1") else "REQUEST_INVALID"
        return JSONResponse(status_code=422, content={"code": code, "message": "요청 값이 허용 범위를 벗어났습니다.", "retryable": False, "request_id": request.state.request_id})

    @app.exception_handler(sqlite3.Error)
    async def persistence_error(request: Request, exc: sqlite3.Error):
        return JSONResponse(status_code=503, content={"code": "PERSISTENCE_UNAVAILABLE", "message": "서비스 데이터를 저장할 수 없습니다.", "retryable": True, "request_id": request.state.request_id})

    async def connection_dependency():
        connection = storage.connect(runtime.database_path)
        try:
            yield connection
        finally:
            connection.close()

    Connection = Annotated[sqlite3.Connection, Depends(connection_dependency)]

    async def current_user(connection: Connection, token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None) -> dict[str, Any]:
        session = storage.get_session(connection, token) if token else None
        if session is None:
            raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
        user = storage.get_user(connection, user_id=session["user_id"])
        if user is None or not user["active"] or _parse_time(session["expires_at"]) <= datetime.now(timezone.utc):
            storage.delete_session(connection, token)
            raise ApiError(401, "SESSION_EXPIRED", "세션이 만료되었거나 계정이 비활성 상태입니다.")
        return user

    CurrentUser = Annotated[dict[str, Any], Depends(current_user)]

    async def developer(user: CurrentUser) -> dict[str, Any]:
        if user["role"] != "developer":
            raise ApiError(403, "ROLE_FORBIDDEN", "developer 권한이 필요합니다.")
        return user

    Developer = Annotated[dict[str, Any], Depends(developer)]

    async def active_handle() -> ReleaseHandle:
        try:
            handle = app.state.index_loader.load()
        except Exception as exc:
            raise IndexUnavailable(f"활성 검색 인덱스를 검증할 수 없습니다: {exc}") from exc
        service_model = getattr(app.state.provider, "embedding_model", runtime.embedding_model)
        if handle.manifest["embedding_model"] != runtime.embedding_model or service_model != runtime.embedding_model or runtime.embedding_model != EMBEDDING_MODEL_CONTRACT:
            raise IndexUnavailable("runtime, provider, and active-index embedding models must match")
        return handle

    def llm_ready() -> bool:
        return runtime.llm_model == LLM_MODEL_CONTRACT and getattr(app.state.provider, "llm_model", runtime.llm_model) == runtime.llm_model

    @app.get("/v1/health/live")
    async def live() -> dict[str, Any]:
        return {"status": "live", "deployment": "local_internal", "process_scope": "single_process"}

    @app.get("/v1/health/ready")
    async def ready(response: Response) -> dict[str, Any]:
        reasons: list[str] = []
        try:
            await active_handle()
        except ApiError as exc:
            reasons.append(exc.message)
        if not llm_ready():
            reasons.append("LLM model binding mismatch")
        if not (runtime.openai_api_key or getattr(app.state.provider, "_client", None) is not None or provider_injected):
            reasons.append("OpenAI provider unavailable")
        if not runtime.bge_model_path.is_dir() and isinstance(app.state.reranker, LocalReranker):
            reasons.append("default BGE artifact unavailable")
        if reasons:
            response.status_code = 503
        return {"status": "ready" if not reasons else "not_ready", "deployment": "local_internal", "process_scope": "single_process", "default_chat": {"available": not reasons, "reasons": reasons}, "auth_profile_available": True, "rate_limit_scope": "single_process"}

    @app.post("/v1/auth/login", response_model=AuthResponse, responses=_error_responses(401, 403, 422, 429, 503))
    async def login(body: LoginRequest, request: Request, response: Response, connection: Connection) -> dict[str, Any]:
        ip = request.client.host if request.client else "unknown"
        if app.state.rate_limiter.blocked(body.username, ip):
            raise ApiError(429, "LOGIN_RATE_LIMITED", "로그인 시도가 너무 많습니다.", retryable=True)
        user = storage.get_user(connection, username=body.username)
        candidate = user if user is not None and user["active"] else app.state.dummy_password
        valid = storage.verify_password(candidate, body.password)
        if user is None or not user["active"] or not valid:
            app.state.rate_limiter.fail(body.username, ip)
            raise ApiError(401, "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.")
        old = request.cookies.get(SESSION_COOKIE)
        if old:
            storage.delete_session(connection, old)
        token = storage.create_session(connection, user["id"])
        response.set_cookie(SESSION_COOKIE, token, max_age=12 * 3600, httponly=True, secure=runtime.cookie_secure, samesite="lax")
        app.state.rate_limiter.clear(body.username, ip)
        return {"user": storage.public_user(user), "profile_state": _profile_state(connection, user)}

    @app.post("/v1/auth/logout", responses=_error_responses(403, 503))
    async def logout(response: Response, connection: Connection, token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None) -> dict[str, bool]:
        if token:
            storage.delete_session(connection, token)
        response.delete_cookie(SESSION_COOKIE, httponly=True, secure=runtime.cookie_secure, samesite="lax")
        return {"logged_out": True}

    @app.get("/v1/auth/session", response_model=AuthResponse, responses=_error_responses(401, 503))
    async def session(user: CurrentUser, connection: Connection) -> dict[str, Any]:
        return {"user": storage.public_user(user), "profile_state": _profile_state(connection, user)}

    @app.get("/v1/catalog/cards", response_model=CatalogResponse, responses=_error_responses(401, 503))
    async def catalog(user: CurrentUser, handle: Annotated[ReleaseHandle, Depends(active_handle)]) -> dict[str, Any]:
        return {"cards": list(handle.catalog), "metadata": _metadata(handle)}

    @app.get("/v1/profile", response_model=ProfileResponse, responses=_error_responses(401, 503))
    async def get_profile(user: CurrentUser, connection: Connection) -> dict[str, Any]:
        return {"profile": storage.get_profile(connection, user["id"]), "profile_state": _profile_state(connection, user)}

    @app.put("/v1/profile", response_model=ProfileResponse, responses=_error_responses(401, 403, 422, 503))
    async def put_profile(body: ProfileUpdate, user: CurrentUser, connection: Connection) -> dict[str, Any]:
        return {"profile": storage.put_profile(connection, user["id"], body.model_dump()), "profile_state": "complete"}

    @app.delete("/v1/profile", responses=_error_responses(401, 403, 503))
    async def remove_profile(user: CurrentUser, connection: Connection) -> dict[str, bool]:
        return {"deleted": storage.delete_profile(connection, user["id"])}

    @app.post("/v1/profile/skip", responses=_error_responses(401, 403, 503))
    async def skip_profile(user: CurrentUser, connection: Connection) -> dict[str, str]:
        storage.skip_onboarding(connection, user["id"])
        return {"profile_state": "skipped"}

    @app.get("/v1/conversations", response_model=ConversationListResponse, responses=_error_responses(401, 503))
    async def conversations(user: CurrentUser, connection: Connection) -> dict[str, Any]:
        return {"conversations": storage.list_conversations(connection, user["id"])}

    @app.post("/v1/conversations", response_model=ConversationResponse, responses=_error_responses(401, 403, 422, 503))
    async def create_conversation(body: ConversationCreate, user: CurrentUser, connection: Connection) -> dict[str, Any]:
        return {"conversation": storage.create_conversation(connection, user["id"], body.title)}

    @app.get("/v1/conversations/{conversation_id}", response_model=ConversationDetailResponse, responses=_error_responses(401, 404, 503))
    async def conversation(conversation_id: str, user: CurrentUser, connection: Connection) -> dict[str, Any]:
        conversation_value = storage.get_conversation(connection, user["id"], conversation_id)
        if conversation_value is None:
            raise ApiError(404, "CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다.")
        messages = storage.list_messages(connection, user["id"], conversation_id)
        for message in messages:
            run_id = (message.get("metadata") or {}).get("run_id")
            if message["role"] == "assistant" and run_id:
                run = storage.get_run(connection, user["id"], run_id)
                if run and run["conversation_id"] == conversation_id and run["result"]:
                    message["result"] = run["result"]
        return {"conversation": conversation_value, "messages": messages}

    @app.delete("/v1/conversations/{conversation_id}", responses=_error_responses(401, 403, 404, 409, 503))
    async def remove_conversation(conversation_id: str, user: CurrentUser, connection: Connection) -> dict[str, bool]:
        if not app.state.account_locks.acquire(user["id"], "user_chat"):
            raise ApiError(409, "RUN_IN_PROGRESS", "이미 처리 중인 대화 요청이 있습니다.", retryable=True)
        try:
            if not storage.delete_conversation(connection, user["id"], conversation_id):
                raise ApiError(404, "CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다.")
            return {"deleted": True}
        finally:
            app.state.account_locks.release(user["id"], "user_chat")

    @app.post("/v1/conversations/{conversation_id}/messages", response_model=ChatResponse, responses=_error_responses(401, 403, 404, 409, 422, 503, chat_failure=True))
    async def chat(conversation_id: str, body: MessageCreate, user: CurrentUser, connection: Connection, handle: Annotated[ReleaseHandle, Depends(active_handle)]) -> dict[str, Any]:
        if not app.state.account_locks.acquire(user["id"], "user_chat"):
            raise ApiError(409, "RUN_IN_PROGRESS", "이미 처리 중인 대화 요청이 있습니다.", retryable=True)
        run = None
        failure_recorded = False
        retrieval = None
        rewrite_status = "raw_first_question"
        trace: dict[str, Any] = {"index": {"release_id": handle.release_id, "manifest_hash": handle.manifest_hash}}
        usage: dict[str, Any] = {}
        try:
            history = storage.list_messages(connection, user["id"], conversation_id)
            created = storage.create_chat_request(connection, user["id"], conversation_id, body.query, asdict(SearchConfig()))
            run = created["run"]
            context = completed_context(history, body.query)
            standalone = body.query
            if len(context) > 1:
                try:
                    standalone, usage["rewrite"] = app.state.provider.rewrite(context)
                    rewrite_status = "rewritten"
                except LlmUnavailable:
                    rewrite_status = "rewrite_failed_fallback"
            storage.update_run(connection, user["id"], run["id"], standalone_query=standalone, rewrite_status=rewrite_status, status="retrieving", usage=usage)
            embedding, embedding_usage = app.state.provider.embed(standalone)
            usage["embedding"] = embedding_usage
            storage.update_run(connection, user["id"], run["id"], usage=usage, status="retrieving")
            retrieval = handle.search(standalone, embedding, SearchConfig())
            trace.update({"query_type": retrieval["query_type"], "stages": retrieval["trace"]})
            answer, answer_usage = app.state.provider.answer(standalone, retrieval["evidence"])
            usage["answer"] = answer_usage
            answer_data = answer.model_dump()
            result = {"run_id": run["id"], "status": "completed", "rewrite_status": rewrite_status, "cards": retrieval["cards"], "answer": answer.answer_text, "recommendations": answer_data["recommendations"], "claims": answer_data["claims"], "evidence": retrieval["evidence"], "query_type": retrieval["query_type"], "citations_semantically_verified": False, "metadata": _metadata(handle)}
            assistant = storage.complete_chat(connection, user["id"], conversation_id, run["id"], answer.answer_text, result, trace, usage)
            return {**result, "conversation_id": conversation_id, "user_message_id": created["user_message"]["id"], "assistant_message_id": assistant["id"]}
        except RagError as exc:
            answer_usage = exc.extra.get("answer_usage", {})
            preview = None
            if run and not failure_recorded:
                failure_recorded = True
                preview = _preview(run["id"], retrieval, rewrite_status) if retrieval else None
                storage.fail_run(connection, user["id"], run["id"], {"code": exc.code, "message": exc.message}, result=preview, trace=trace, usage={**usage, "answer": answer_usage}, diagnostic="검색 진단 미리보기 · 추천 완료 아님" if preview else None)
            raise ApiError(503, exc.code, exc.message, retryable=exc.retryable, run_id=run["id"] if run else None, retrieval_preview=preview) from exc
        except LookupError as exc:
            raise ApiError(404, "CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다.", run_id=run["id"] if run else None) from exc
        finally:
            app.state.account_locks.release(user["id"], "user_chat")

    @app.get("/internal/v1/lab/options", responses=_error_responses(401, 403, 503))
    async def lab_options(user: Developer) -> dict[str, Any]:
        return {"profile": ["benefit_hierarchy"], "vector_weight": [0, .2, .4, .6, .8, 1], "component_depth": [50], "candidate_depth": [10, 20, 50], "top_k": [1, 3, 5], "reranker": ["off", "bge", "gte"], "reranker_route": ["selective", "all"], "include_llm_default": False, "llm_models": [LLM_MODEL_CONTRACT], "production_default": LabConfig().model_dump(), "label": "development retrieval diagnostics; not a product success criterion."}

    @app.post("/internal/v1/lab/runs", responses=_error_responses(401, 403, 404, 409, 422, 503))
    async def run_lab(body: LabRunRequest, user: Developer, connection: Connection, handle: Annotated[ReleaseHandle, Depends(active_handle)]) -> dict[str, Any]:
        if not app.state.account_locks.acquire(user["id"], "developer_lab"):
            raise ApiError(409, "RUN_IN_PROGRESS", "이미 처리 중인 Lab 요청이 있습니다.", retryable=True)
        run = None
        failure_recorded = False
        usage: dict[str, Any] = {}
        trace: dict[str, Any] = {"index": {"release_id": handle.release_id, "manifest_hash": handle.manifest_hash}}

        def record_failure(code: str, message: str, answer_usage: dict[str, Any] | None = None) -> None:
            nonlocal failure_recorded
            if run is None or failure_recorded:
                return
            failure_recorded = True
            current = storage.get_run(connection, user["id"], run["id"])
            if current is None:
                raise LookupError("run not found")
            if current["status"] in {"completed", "failed"}:
                return
            storage.fail_run(connection, user["id"], run["id"], {"code": code, "message": message}, trace=trace, usage={**usage, "answer": answer_usage or {}})

        try:
            config = body.config.search_config()
            run = storage.create_lab_run(connection, user["id"], body.query, {"requested": body.config.model_dump(), "comparison_contract_pending": True})
            artifact = app.state.reranker.artifact_contract(config.reranker) if config.reranker != "off" else None
            material = comparison_config(config, IndexIdentity(handle.release_id, handle.manifest_hash, handle.manifest["embedding_model"]), runtime_embedding_model=runtime.embedding_model, embedding_model_match=True, reranker_artifact=artifact)
            run = storage.update_run(connection, user["id"], run["id"], config=material)
            embedding = None
            if config.vector_weight:
                embedding, usage["embedding"] = app.state.provider.embed(body.query)
            result = handle.search(body.query, embedding, config)
            metrics = retrieval_metrics([card["card_key"] for card in result["cards"]], result["evidence"], None, valid_card_keys={card["card_key"] for card in handle.catalog}, top_k=config.top_k)
            trace.update({"query_type": result["query_type"], "stages": result["trace"], "metrics": metrics})
            answer_data = None
            if body.config.include_llm:
                if not llm_ready():
                    raise LlmUnavailable("LLM model binding mismatch")
                answer, usage["answer"] = app.state.provider.answer(body.query, result["evidence"])
                answer_data = answer.model_dump()
            saved_result = {"run_id": run["id"], "status": "completed", "cards": result["cards"], "evidence": result["evidence"], "query_type": result["query_type"], "metrics": metrics, "answer": answer_data, "citations_semantically_verified": False}
            completed = storage.complete_lab(connection, user["id"], run["id"], saved_result, trace, usage, answer_data["answer_text"] if answer_data else None)
            return {**saved_result, "trace": trace, "config_hash": completed["config_hash"], "counts": storage.lab_counts(connection, user["id"], completed["config_hash"]), "metadata": _metadata(handle)}
        except RagError as exc:
            try:
                record_failure(exc.code, exc.message, exc.extra.get("answer_usage"))
            except LookupError as persistence_exc:
                raise ApiError(404, "RUN_NOT_FOUND", "Lab run을 찾을 수 없습니다.", run_id=run["id"] if run else None) from persistence_exc
            except sqlite3.Error as persistence_exc:
                raise ApiError(503, "PERSISTENCE_UNAVAILABLE", "서비스 데이터를 저장할 수 없습니다.", retryable=True, run_id=run["id"] if run else None) from persistence_exc
            raise ApiError(503, exc.code, exc.message, retryable=exc.retryable, run_id=run["id"] if run else None) from exc
        except LookupError as exc:
            try:
                record_failure("RUN_NOT_FOUND", "Lab run을 찾을 수 없습니다.")
            except (LookupError, sqlite3.Error):
                pass
            raise ApiError(404, "RUN_NOT_FOUND", "Lab run을 찾을 수 없습니다.", run_id=run["id"] if run else None) from exc
        except sqlite3.Error as exc:
            try:
                record_failure("PERSISTENCE_UNAVAILABLE", "서비스 데이터를 저장할 수 없습니다.")
            except (LookupError, sqlite3.Error):
                pass
            raise ApiError(503, "PERSISTENCE_UNAVAILABLE", "서비스 데이터를 저장할 수 없습니다.", retryable=True, run_id=run["id"] if run else None) from exc
        finally:
            app.state.account_locks.release(user["id"], "developer_lab")

    @app.get("/internal/v1/lab/runs", responses=_error_responses(401, 403, 503))
    async def lab_history(user: Developer, connection: Connection) -> dict[str, Any]:
        return {"runs": storage.list_runs(connection, user["id"], "developer_lab")}

    @app.get("/internal/v1/lab/runs/{run_id}", responses=_error_responses(401, 403, 404, 503))
    async def lab_detail(run_id: str, user: Developer, connection: Connection) -> dict[str, Any]:
        run = storage.get_run(connection, user["id"], run_id)
        if run is None or run["run_kind"] != "developer_lab":
            raise ApiError(404, "RUN_NOT_FOUND", "Lab run을 찾을 수 없습니다.")
        return {"run": run, "counts": storage.lab_counts(connection, user["id"], run["config_hash"])}

    @app.delete("/internal/v1/lab/runs/{run_id}", responses=_error_responses(401, 403, 404, 409, 503))
    async def delete_lab(run_id: str, user: Developer, connection: Connection) -> dict[str, bool]:
        if not app.state.account_locks.acquire(user["id"], "developer_lab"):
            raise ApiError(409, "RUN_IN_PROGRESS", "이미 처리 중인 Lab 요청이 있습니다.", retryable=True)
        try:
            run = storage.get_run(connection, user["id"], run_id)
            if run is None or run["run_kind"] != "developer_lab":
                raise ApiError(404, "RUN_NOT_FOUND", "Lab run을 찾을 수 없습니다.")
            if run["status"] not in {"completed", "failed"}:
                raise ApiError(409, "RUN_IN_PROGRESS", "완료되지 않은 Lab run은 삭제할 수 없습니다.", retryable=True, run_id=run_id)
            if not storage.delete_run(connection, user["id"], run_id):
                raise ApiError(404, "RUN_NOT_FOUND", "Lab run을 찾을 수 없습니다.")
            return {"deleted": True}
        finally:
            app.state.account_locks.release(user["id"], "developer_lab")

    return app


app = create_app()
