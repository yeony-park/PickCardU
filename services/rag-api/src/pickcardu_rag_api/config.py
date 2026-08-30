from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _truthy(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    public_operation_approved: bool
    database_path: Path
    index_runtime_root: Path
    allowed_origins: tuple[str, ...]
    cookie_secure: bool
    allow_missing_origin_for_tests: bool
    openai_api_key: str | None
    embedding_model: str
    llm_model: str
    bge_model_path: Path
    gte_model_path: Path | None = None
    gte_allow_custom_code: bool = False


def validate_settings(settings: Settings) -> Settings:
    try:
        settings.database_path.resolve().relative_to(settings.index_runtime_root.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("service database must be outside the index runtime root")
    if settings.environment == "production":
        raise ValueError("production is unsupported; this service is local_internal and single_process only")
    return settings


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    source = os.environ if environ is None else environ
    service_root = Path(__file__).resolve().parents[2]
    repository_root = service_root.parents[1]
    gte_path = source.get("PICKCARDU_GTE_MODEL_PATH", "").strip()
    return validate_settings(Settings(
        environment=source.get("PICKCARDU_ENV", "development").strip().casefold(),
        public_operation_approved=_truthy(source.get("PICKCARDU_PUBLIC_OPERATION_APPROVED")),
        database_path=Path(source.get("PICKCARDU_DB_PATH", service_root / "data/pickcardu.sqlite3")),
        index_runtime_root=Path(source.get("PICKCARDU_INDEX_RUNTIME_ROOT", repository_root / "data/rag/runtime")),
        allowed_origins=tuple(value.strip() for value in source.get("PICKCARDU_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if value.strip()),
        cookie_secure=_truthy(source.get("PICKCARDU_COOKIE_SECURE")),
        allow_missing_origin_for_tests=_truthy(source.get("PICKCARDU_ALLOW_MISSING_ORIGIN_FOR_TESTS")),
        openai_api_key=source.get("OPENAI_API_KEY") or None,
        embedding_model=source.get("PICKCARDU_EMBEDDING_MODEL", "text-embedding-3-small"),
        llm_model=source.get("PICKCARDU_LLM_MODEL", "gpt-5.6-luna"),
        bge_model_path=Path(source.get("PICKCARDU_BGE_MODEL_PATH", repository_root / ".cache/reranker/bge-reranker-v2-m3")),
        gte_model_path=Path(gte_path) if gte_path else None,
        gte_allow_custom_code=_truthy(source.get("PICKCARDU_GTE_ALLOW_CUSTOM_CODE")),
    ))
