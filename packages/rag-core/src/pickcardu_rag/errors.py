"""Transport-independent RAG failures with stable machine codes."""

from __future__ import annotations

from typing import Any


class RagError(Exception):
    code = "RAG_ERROR"
    retryable = False

    def __init__(self, message: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra or {}


class RerankerUnavailable(RagError):
    code = "RERANKER_UNAVAILABLE"


class EmbeddingUnavailable(RagError):
    code = "EMBEDDING_UNAVAILABLE"
    retryable = True


class LlmUnavailable(RagError):
    code = "LLM_UNAVAILABLE"
    retryable = True


class LlmUngrounded(LlmUnavailable):
    code = "LLM_UNGROUNDED"
