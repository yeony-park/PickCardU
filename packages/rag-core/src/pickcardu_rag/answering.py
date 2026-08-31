"""Injectable OpenAI boundary and grounded answer contracts."""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import EmbeddingUnavailable, LlmUnavailable, LlmUngrounded


ANSWER_PAYLOAD_UNIT = "utf8_bytes_conservative"
ANSWER_PAYLOAD_UNIT_LIMIT = 12_000
EMBEDDING_MODEL_CONTRACT = "text-embedding-3-small"
LLM_MODEL_CONTRACT = "gpt-5.6-luna"
Citation = Annotated[str, Field(min_length=1, max_length=64)]
Condition = Annotated[str, Field(min_length=1, max_length=60)]
ShortValue = Annotated[str, Field(max_length=40)]


class RewriteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    standalone_query: str = Field(min_length=1, max_length=500)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_key: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=100)
    citations: list[Citation] = Field(min_length=1, max_length=2)

    @field_validator("card_key", "reason")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class AtomicClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_key: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=120)
    value: ShortValue | float | int | None = None
    unit: str | None = Field(default=None, max_length=20)
    conditions: list[Condition] = Field(default_factory=list, max_length=2)
    citations: list[Citation] = Field(min_length=1, max_length=2)

    @field_validator("card_key", "text")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class AnswerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_status: Literal["answered", "insufficient_evidence"] = "answered"
    answer_text: str = Field(min_length=1, max_length=400)
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=5)
    claims: list[AtomicClaim] = Field(default_factory=list, max_length=5)

    @field_validator("answer_text")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("answer_text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_answer_state(self) -> "AnswerOutput":
        if self.answer_status == "answered" and not self.claims:
            raise ValueError("answered response requires at least one grounded claim")
        if self.answer_status == "insufficient_evidence" and (self.recommendations or self.claims):
            raise ValueError("insufficient response must not contain recommendations or claims")
        return self


def build_answer_payload(standalone_query: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "standalone_query": standalone_query,
        "evidence": [
            {key: item[key] for key in ("card_key", "card_name", "issuer", "chunk_id", "text")}
            for item in evidence
        ],
    }


def serialize_answer_payload(standalone_query: str, evidence: list[dict[str, Any]]) -> str:
    return json.dumps(build_answer_payload(standalone_query, evidence), ensure_ascii=False, separators=(",", ":"))


def measure_answer_payload(standalone_query: str, evidence: list[dict[str, Any]]) -> tuple[int, str]:
    return len(serialize_answer_payload(standalone_query, evidence).encode("utf-8")), ANSWER_PAYLOAD_UNIT


def completed_context(
    messages: list[dict[str, Any]], current_question: str, *, max_pairs: int = 2, max_chars: int = 6000
) -> list[dict[str, str]]:
    pairs: list[tuple[dict[str, str], dict[str, str]]] = []
    pending: dict[str, str] | None = None
    for message in messages:
        if message.get("role") == "user":
            pending = {"role": "user", "content": str(message.get("content", ""))}
        elif message.get("role") == "assistant" and pending is not None:
            pairs.append((pending, {"role": "assistant", "content": str(message.get("content", ""))}))
            pending = None
    selected: list[tuple[dict[str, str], dict[str, str]]] = []
    used = 0
    for pair in reversed(pairs):
        size = len(pair[0]["content"]) + len(pair[1]["content"])
        if size > max_chars or (selected and used + size > max_chars):
            break
        selected.append(pair)
        used += size
        if len(selected) == max_pairs:
            break
    return [message for pair in reversed(selected) for message in pair] + [
        {"role": "user", "content": current_question}
    ]


def validate_grounding(answer: AnswerOutput, evidence: list[dict[str, Any]]) -> AnswerOutput:
    if answer.answer_status == "insufficient_evidence":
        if answer.recommendations or answer.claims:
            raise ValueError("insufficient response contains grounded output")
        return answer
    chunk_to_card = {item["chunk_id"]: item["card_key"] for item in evidence}
    valid_cards = set(chunk_to_card.values())
    for item in [*answer.recommendations, *answer.claims]:
        if item.card_key not in valid_cards or not all(
            chunk_to_card.get(citation) == item.card_key for citation in item.citations
        ):
            raise ValueError("answer evidence ownership mismatch")
    if not answer.claims:
        raise ValueError("answer has zero grounded claims")
    return answer


def _usage(response: Any) -> dict[str, Any]:
    value = getattr(response, "usage", None)
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value) if isinstance(value, dict) else {}


def _is_incomplete_json(error: ValidationError) -> bool:
    errors = error.errors()
    return bool(errors) and all(
        item.get("type") == "json_invalid"
        and "eof while parsing" in str((item.get("ctx") or {}).get("error", "")).casefold()
        for item in errors
    )


def _with_retry_answer_usage(error: LlmUnavailable, started: float) -> LlmUnavailable:
    error.extra["answer_usage"] = {
        "attempt_count": 2,
        "usage_complete": False,
        "usage_scope": "unavailable",
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "max_output_tokens_per_attempt": 2400,
    }
    return error


class OpenAIService:
    """Provider client is injectable; construction never performs network I/O."""

    def __init__(
        self,
        *,
        api_key: str | None,
        embedding_model: str = EMBEDDING_MODEL_CONTRACT,
        llm_model: str = LLM_MODEL_CONTRACT,
        client: Any = None,
    ) -> None:
        self.api_key = api_key
        self.embedding_model = embedding_model
        self.llm_model = llm_model
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise LlmUnavailable("OPENAI_API_KEY is not configured")
        from openai import OpenAI

        self._client = OpenAI(api_key=self.api_key, max_retries=0)
        return self._client

    def embed(self, query: str) -> tuple[np.ndarray, dict[str, Any]]:
        started = time.perf_counter()
        try:
            response = self._get_client().embeddings.create(
                input=query,
                model=self.embedding_model,
                dimensions=1536,
                encoding_format="float",
                timeout=20.0,
            )
            vector = np.asarray(response.data[0].embedding, dtype=np.float32)
            if vector.shape != (1536,) or not np.isfinite(vector).all():
                raise ValueError("invalid embedding response")
            return vector, {
                "model": self.embedding_model,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "usage": _usage(response),
            }
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable(f"query embedding failed: {type(exc).__name__}") from exc

    def rewrite(self, context: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        started = time.perf_counter()
        try:
            response = self._get_client().responses.parse(
                model=self.llm_model,
                instructions="대화 문맥을 반영해 현재 카드 혜택 질문만 독립형 질의로 바꾸세요. 새 사실을 추가하지 마세요.",
                input=context,
                text_format=RewriteOutput,
                tools=[],
                store=False,
                max_output_tokens=300,
                timeout=60.0,
            )
            parsed = response.output_parsed
            if not isinstance(parsed, RewriteOutput):
                parsed = RewriteOutput.model_validate(parsed)
            return parsed.standalone_query, {
                "model": self.llm_model,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "usage": _usage(response),
            }
        except Exception as exc:
            raise LlmUnavailable(f"query rewrite failed: {type(exc).__name__}") from exc

    def answer(self, standalone_query: str, evidence: list[dict[str, Any]]) -> tuple[AnswerOutput, dict[str, Any]]:
        started = time.perf_counter()
        payload_size, payload_unit = measure_answer_payload(standalone_query, evidence)
        if payload_size > ANSWER_PAYLOAD_UNIT_LIMIT:
            raise LlmUnavailable("answer evidence payload exceeds the 12000-byte conservative limit")
        instructions = (
            "제공된 evidence만 사용해 한국어로 매우 간결하게 답하세요. 모든 atomic claim과 추천은 card_key와 citations를 넣으세요. "
            "각 citation chunk는 해당 claim 또는 추천의 card_key와 같아야 합니다. "
            "각 추천 카드의 핵심 조건 중심으로 작성하고, evidence에 포함된 카드 중 추천은 최대 5개, "
            "atomic claim은 1~5개, "
            "항목당 citations는 최대 2개로 제한하세요. 공식 상품설명서 재확인이 필요함을 밝히세요. "
            "질문을 직접 뒷받침하는 근거가 부족하면 answer_status를 insufficient_evidence로 설정하고 "
            "recommendations와 claims를 비운 뒤 현재 등록된 카드 문서에서 확인하기 어렵다고 답하세요."
        )
        retry_instructions = (
            f"{instructions} 이전 출력이 잘렸으므로 답변, 추천 이유, claim, 조건을 첫 시도보다 더 짧게 작성하세요."
        )
        request = {
            "model": self.llm_model,
            "input": [{"role": "user", "content": serialize_answer_payload(standalone_query, evidence)}],
            "text_format": AnswerOutput,
            "tools": [],
            "store": False,
            "max_output_tokens": 2400,
            "timeout": 60.0,
        }
        for attempt_count in (1, 2):
            try:
                response = self._get_client().responses.parse(
                    **request, instructions=instructions if attempt_count == 1 else retry_instructions
                )
                parsed = response.output_parsed
                if parsed is None:
                    raise LlmUngrounded("answer response was refused or empty")
                if not isinstance(parsed, AnswerOutput):
                    parsed = AnswerOutput.model_validate(parsed)
                validate_grounding(parsed, evidence)
                return parsed, {
                    "model": self.llm_model,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "usage": _usage(response),
                    "input_payload_unit": payload_unit,
                    "input_payload_size": payload_size,
                    "attempt_count": attempt_count,
                    "usage_complete": attempt_count == 1,
                    "usage_scope": "all_attempts" if attempt_count == 1 else "successful_attempt_only",
                    "max_output_tokens_per_attempt": 2400,
                }
            except ValidationError as exc:
                if attempt_count == 1 and _is_incomplete_json(exc):
                    continue
                error = LlmUngrounded(str(exc))
                raise (_with_retry_answer_usage(error, started) if attempt_count == 2 else error) from exc
            except LlmUnavailable as exc:
                if attempt_count == 2:
                    _with_retry_answer_usage(exc, started)
                raise
            except ValueError as exc:
                error = LlmUngrounded(str(exc))
                raise (_with_retry_answer_usage(error, started) if attempt_count == 2 else error) from exc
            except Exception as exc:
                error = LlmUnavailable(f"answer generation failed: {type(exc).__name__}")
                raise (_with_retry_answer_usage(error, started) if attempt_count == 2 else error) from exc
        raise AssertionError("answer retry loop exhausted")
