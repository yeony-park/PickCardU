from __future__ import annotations

class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False, run_id: str | None = None, retrieval_preview: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.run_id = run_id
        self.retrieval_preview = retrieval_preview


class IndexUnavailable(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__(503, "INDEX_UNAVAILABLE", message)
