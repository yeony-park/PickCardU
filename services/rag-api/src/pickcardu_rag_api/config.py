from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from pickcardu_rag.answering import ANSWER_PAYLOAD_UNIT_LIMIT, answer_payload_limit


@dataclass(frozen=True)
class Settings:
    environment: str
    index_runtime_root: Path
    allowed_origins: tuple[str, ...]
    openai_api_key: str | None
    embedding_model: str
    llm_model: str
    bge_model_path: Path
    chat_db_path: Path | None = None
    answer_payload_bytes: int = ANSWER_PAYLOAD_UNIT_LIMIT


def validate_settings(settings: Settings) -> Settings:
    if settings.environment == "production":
        raise ValueError("production deployment is not configured; use development or test")
    if not settings.allowed_origins:
        raise ValueError("at least one allowed origin is required")
    if type(settings.answer_payload_bytes) is not int or settings.answer_payload_bytes <= 0:
        raise ValueError('answer payload byte limit must be a positive integer')
    return settings


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    source = os.environ if environ is None else environ
    service_root = Path(__file__).resolve().parents[2]
    repository_root = service_root.parents[1]
    return validate_settings(Settings(
        environment=source.get("PICKCARDU_ENV", "development").strip().casefold(),
        index_runtime_root=Path(source.get("PICKCARDU_INDEX_RUNTIME_ROOT", repository_root / "data/rag/runtime")),
        allowed_origins=tuple(
            value.strip()
            for value in source.get(
                "PICKCARDU_ALLOWED_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000",
            ).split(",")
            if value.strip()
        ),
        openai_api_key=source.get("OPENAI_API_KEY") or None,
        embedding_model=source.get("PICKCARDU_EMBEDDING_MODEL", "text-embedding-3-small"),
        llm_model=source.get("PICKCARDU_LLM_MODEL", "gpt-5.6-luna"),
        bge_model_path=Path(
            source.get("PICKCARDU_BGE_MODEL_PATH", repository_root / ".cache/reranker/bge-reranker-v2-m3")
        ),
        chat_db_path=Path(source.get('PICKCARDU_CHAT_DB_PATH', repository_root / 'data/chat/runtime/chat.sqlite')),
        answer_payload_bytes=answer_payload_limit(source),
    ))
