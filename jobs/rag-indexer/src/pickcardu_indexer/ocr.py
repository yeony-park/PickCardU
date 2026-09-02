from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


LUNA_OCR_MODEL = "gpt-5.6-luna"
LUNA_REASONING = "max"
STRUCTURE_MODEL = "gpt-5.6-luna"
STRUCTURE_REASONING = "max"
UPSTAGE_ENDPOINT = "https://api.upstage.ai/v1/document-digitization"
UPSTAGE_MODEL = "document-parse"
MAX_MODEL_OUTPUT_TOKENS = 128_000
OUTPUT_TOKENS_PER_PAGE = 4_000

OCR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page", "text", "uncertain_spans"],
                "properties": {
                    "page": {"type": "integer", "minimum": 1},
                    "text": {"type": "string"},
                    "uncertain_spans": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["text", "reason"],
                            "properties": {
                                "text": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
    },
}

_FACT_PROPERTIES = {
    field: {"type": "string"}
    for field in ("target", "condition", "value", "unit", "cap", "frequency", "period", "exceptions")
}
STRUCTURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["identity", "facts", "span_dispositions"],
    "properties": {
        "identity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["issuer_name", "card_name", "issuer_evidence", "card_evidence"],
            "properties": {
                "issuer_name": {"type": "string"},
                "card_name": {"type": "string"},
                "issuer_evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["page", "quote"],
                    "properties": {"page": {"type": "integer", "minimum": 1}, "quote": {"type": "string"}},
                },
                "card_evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["page", "quote"],
                    "properties": {"page": {"type": "integer", "minimum": 1}, "quote": {"type": "string"}},
                },
            },
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [*_FACT_PROPERTIES, "evidence"],
                "properties": {
                    **_FACT_PROPERTIES,
                    "evidence": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["page", "quote"],
                        "properties": {"page": {"type": "integer", "minimum": 1}, "quote": {"type": "string"}},
                    },
                },
            },
        },
        "span_dispositions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page", "quote", "kind", "reason"],
                "properties": {
                    "page": {"type": "integer", "minimum": 1},
                    "quote": {"type": "string"},
                    "kind": {"type": "string", "enum": ["fact", "identity", "ignore"]},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

OCR_PROMPT = """원본 카드 상품안내서의 모든 페이지를 순서대로 정확히 전사하세요.
보이는 문자만 기록하고 요약, 교정, 추측, 보완하지 마세요. 숫자, 단위, 기호, 표, 각주와 제외 조건을 보이는 그대로 유지하세요.
판독할 수 없는 문자는 �로 기록하고 uncertain_spans에 이유를 남기세요. 지정된 JSON 형식만 반환하세요."""

STRUCTURE_PROMPT = """주어진 단일 OCR lane만 사용해 카드 혜택을 구조화하세요. 다른 OCR 결과를 추측하거나 보완하지 마세요.
issuer_name과 card_name을 추출하고, 혜택별 target, condition, value, unit, cap, frequency, period, exceptions를 문자열로 기록하세요.
각 identity와 fact의 quote는 해당 page OCR 본문에 실제로 존재하는 정확한 연속 인용문이어야 합니다.
OCR의 모든 비어 있지 않은 줄을 span_dispositions에 정확히 한 번 기록하고, fact/identity가 아닌 줄은 ignore와 구체적인 reason을 사용하세요.
숫자, 단위, 조건과 예외를 정규화하거나 확대 해석하지 말고 지정된 JSON 형식만 반환하세요."""


class OcrProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def page_scaled_output_tokens(page_count: int, *, floor: int) -> int:
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1 or floor < 1:
        raise ValueError("output token policy requires positive page count and floor")
    return min(MAX_MODEL_OUTPUT_TOKENS, max(floor, page_count * OUTPUT_TOKENS_PER_PAGE))


def _write_once(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != value:
            raise RuntimeError(f"immutable OCR artifact changed: {path}")
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def _response_dict(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    if isinstance(response, dict):
        return response
    raise OcrProviderError("provider response is not serializable")


def _output_json(response: Any, raw: dict[str, Any]) -> dict[str, Any]:
    text = getattr(response, "output_text", None)
    if not isinstance(text, str):
        text = "\n".join(
            str(content.get("text", ""))
            for item in raw.get("output", [])
            if isinstance(item, dict) and item.get("type") == "message"
            for content in item.get("content", [])
            if isinstance(content, dict) and content.get("type") == "output_text"
        )
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise OcrProviderError("provider did not return valid structured JSON") from error
    if not isinstance(value, dict):
        raise OcrProviderError("provider structured output must be an object")
    return value


def _cached_json(root: Path, pattern: str) -> dict[str, Any] | None:
    paths = sorted(root.glob(pattern))
    if len(paths) > 1:
        raise RuntimeError(f"multiple immutable OCR artifacts match {pattern}")
    if not paths:
        return None
    value = json.loads(paths[0].read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"cached OCR artifact is not an object: {paths[0]}")
    return value


def pdf_page_count(path: Path) -> int:
    import pymupdf

    with pymupdf.open(path) as document:
        if document.page_count < 1:
            raise ValueError("source PDF has no pages")
        return document.page_count


def validate_pages(value: Any, expected_count: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("OCR pages must be an array")
    pages: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("OCR page must be an object")
        page = row.get("page", row.get("page_num"))
        text = row.get("text", row.get("markdown"))
        uncertain = row.get("uncertain_spans", [])
        if isinstance(page, bool) or not isinstance(page, int) or not isinstance(text, str) or not isinstance(uncertain, list):
            raise ValueError("OCR page fields are invalid")
        pages.append({"page": page, "text": text, "uncertain_spans": uncertain})
    pages.sort(key=lambda row: row["page"])
    expected = list(range(1, expected_count + 1))
    if [row["page"] for row in pages] != expected:
        raise ValueError("OCR pages are missing, duplicated, or out of range")
    return pages


def pages_text(pages: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"=== PAGE {row['page']} ===\n{row['text']}" for row in pages).rstrip() + "\n"


def _element_text(element: dict[str, Any]) -> str:
    content = element.get("content")
    if isinstance(content, dict):
        return str(content.get("markdown") or content.get("html") or content.get("text") or "").strip()
    return str(content or element.get("markdown") or element.get("html") or element.get("text") or "").strip()


def upstage_pages(raw: dict[str, Any], expected_count: int) -> list[dict[str, Any]]:
    elements = raw.get("elements")
    if not isinstance(elements, list) and isinstance(raw.get("content"), dict):
        elements = raw["content"].get("elements")
    if not isinstance(elements, list):
        elements = []
    usage = raw.get("usage")
    if isinstance(usage, dict) and usage.get("pages") != expected_count:
        raise ValueError("Upstage reported page count does not match the source PDF")
    grouped: dict[int, list[str]] = {page: [] for page in range(1, expected_count + 1)}
    for element in elements:
        if not isinstance(element, dict):
            raise ValueError("Upstage element must be an object")
        raw_page = element.get("page", element.get("page_number"))
        if raw_page is None:
            raise ValueError("Upstage element page number is required")
        try:
            page = int(raw_page)
        except (TypeError, ValueError) as error:
            raise ValueError("Upstage element page number is invalid") from error
        if page not in grouped:
            raise ValueError("Upstage element page is outside the source PDF")
        text = _element_text(element)
        if text:
            grouped[page].append(text)
    if expected_count == 1 and not grouped[1]:
        content = raw.get("content")
        if isinstance(content, dict):
            fallback = str(content.get("markdown") or content.get("text") or "").strip()
            if fallback:
                grouped[1].append(fallback)
    empty_pages = [page for page, rows in grouped.items() if not rows]
    if empty_pages:
        raise ValueError(f"Upstage response has no text for source pages: {empty_pages}")
    return validate_pages(
        [{"page": page, "text": "\n".join(grouped[page]), "uncertain_spans": []} for page in grouped],
        expected_count,
    )


class LunaOcrTranscriber:
    provider = "luna"

    def __init__(self, api_key: str, *, model: str = LUNA_OCR_MODEL, reasoning: str = LUNA_REASONING, client: Any = None) -> None:
        self.api_key, self.model, self.reasoning, self._client = api_key, model, reasoning, client

    @property
    def config(self) -> dict[str, Any]:
        return {"endpoint": "openai.responses", "model": self.model, "reasoning": self.reasoning, "prompt_sha256": _sha256(OCR_PROMPT.encode()), "schema_sha256": _sha256(_json_bytes(OCR_SCHEMA)), "max_output_policy": "page_scaled_4000_floor_12000_cap_128000"}

    def request(self, source: Path) -> tuple[dict[str, Any], int]:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, max_retries=0)
        try:
            count = pdf_page_count(source)
            encoded = base64.b64encode(source.read_bytes()).decode("ascii")
            response = self._client.responses.create(
                model=self.model,
                reasoning={"effort": self.reasoning},
                input=[{"role": "user", "content": [
                    {"type": "input_file", "filename": source.name, "file_data": f"data:application/pdf;base64,{encoded}"},
                    {"type": "input_text", "text": f"{OCR_PROMPT}\nPDF page count: {count}"},
                ]}],
                text={"format": {"type": "json_schema", "name": "ocr_pages", "strict": True, "schema": OCR_SCHEMA}},
                store=False,
                max_output_tokens=page_scaled_output_tokens(count, floor=12_000),
                timeout=900.0,
            )
        except Exception as error:
            retryable = not isinstance(error, (OSError, ValueError))
            raise OcrProviderError(f"Luna OCR failed: {type(error).__name__}: {error}", retryable=retryable) from error
        return _response_dict(response), count

    def parse(self, raw: dict[str, Any], expected_count: int) -> list[dict[str, Any]]:
        output = _output_json(None, raw)
        return validate_pages(output.get("pages"), expected_count)

    def transcribe(self, source: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        raw, count = self.request(source)
        return raw, self.parse(raw, count), self.config


def _multipart_body(source: Path) -> tuple[bytes, str]:
    boundary = f"----pickcardu-{uuid.uuid4().hex}"
    fields = {"model": UPSTAGE_MODEL, "ocr": "force", "coordinates": "true", "output_formats": '["html","markdown"]'}
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode(), b"\r\n"])
    mime = mimetypes.guess_type(source.name)[0] or "application/pdf"
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="document"; filename="{source.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(), source.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), boundary


class UpstageOcrTranscriber:
    provider = "upstage"

    def __init__(self, api_key: str, *, opener: Any = None) -> None:
        self.api_key, self._opener = api_key, opener or urllib.request.urlopen

    @property
    def config(self) -> dict[str, Any]:
        return {"endpoint": UPSTAGE_ENDPOINT, "model": UPSTAGE_MODEL, "ocr": "force", "coordinates": True, "output_formats": ["html", "markdown"]}

    def request(self, source: Path) -> tuple[dict[str, Any], int]:
        try:
            count = pdf_page_count(source)
            body, boundary = _multipart_body(source)
            request = urllib.request.Request(UPSTAGE_ENDPOINT, data=body, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
            with self._opener(request, timeout=900) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise OcrProviderError(f"Upstage OCR HTTP {error.code}: {detail[-1000:]}", retryable=error.code == 429 or error.code >= 500) from error
        except Exception as error:
            retryable = not isinstance(error, (OSError, ValueError))
            raise OcrProviderError(f"Upstage OCR failed: {type(error).__name__}: {error}", retryable=retryable) from error
        if not isinstance(raw, dict):
            raise OcrProviderError("Upstage response must be an object")
        return raw, count

    def parse(self, raw: dict[str, Any], expected_count: int) -> list[dict[str, Any]]:
        return upstage_pages(raw, expected_count)

    def transcribe(self, source: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        raw, count = self.request(source)
        return raw, self.parse(raw, count), self.config


class LunaFactStructurer:
    def __init__(self, api_key: str, *, model: str = STRUCTURE_MODEL, reasoning: str = STRUCTURE_REASONING, client: Any = None) -> None:
        self.api_key, self.model, self.reasoning, self._client = api_key, model, reasoning, client

    @property
    def config(self) -> dict[str, Any]:
        return {"endpoint": "openai.responses", "model": self.model, "reasoning": self.reasoning, "prompt_sha256": _sha256(STRUCTURE_PROMPT.encode()), "schema_sha256": _sha256(_json_bytes(STRUCTURE_SCHEMA)), "max_output_policy": "page_scaled_4000_floor_16000_cap_128000"}

    def request(self, provider: str, pages: list[dict[str, Any]]) -> dict[str, Any]:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, max_retries=0)
        source = json.dumps([{"page": row["page"], "text": row["text"]} for row in pages], ensure_ascii=False)
        try:
            response = self._client.responses.create(
                model=self.model,
                reasoning={"effort": self.reasoning},
                input=[{"role": "user", "content": [{"type": "input_text", "text": f"{STRUCTURE_PROMPT}\nLane: {provider}\nOCR pages JSON:\n{source}"}]}],
                text={"format": {"type": "json_schema", "name": "card_facts", "strict": True, "schema": STRUCTURE_SCHEMA}},
                store=False,
                max_output_tokens=page_scaled_output_tokens(len(pages), floor=16_000),
                timeout=900.0,
            )
        except Exception as error:
            raise OcrProviderError(f"Luna structuring failed for {provider}: {type(error).__name__}: {error}", retryable=True) from error
        return _response_dict(response)

    def parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        return _output_json(None, raw)

    def structure(self, provider: str, pages: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = self.request(provider, pages)
        return raw, self.parse(raw)


class LiveLaneAdapter:
    def __init__(self, provider: str, sources: dict[str, Path], root: Path, transcriber: Any, structurer: LunaFactStructurer) -> None:
        if provider != getattr(transcriber, "provider", None):
            raise ValueError("live lane provider mismatch")
        self.provider, self.sources, self.root, self.transcriber, self.structurer = provider, sources, root, transcriber, structurer
        self.ocr_config_hash = _sha256(_json_bytes(transcriber.config))
        self.structure_config_hash = _sha256(_json_bytes(structurer.config))
        self.config_hash = _sha256(_json_bytes({"ocr": transcriber.config, "structure": structurer.config}))

    def _root(self, document_id: str, source_hash: str) -> Path:
        return self.root / document_id.replace("/", "__") / self.provider / source_hash / self.ocr_config_hash

    def _structure_root(self, document_id: str, source_hash: str) -> Path:
        return self._root(document_id, source_hash) / "structure" / self.structure_config_hash

    def load(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        source = self.sources[document_id]
        try:
            source_hash = _sha256_file(source)
            expected_count = pdf_page_count(source)
        except Exception as error:
            raise OcrProviderError(f"source PDF preflight failed: {type(error).__name__}: {error}") from error
        root = self._root(document_id, source_hash)
        structure_root = self._structure_root(document_id, source_hash)
        normalized_path = structure_root / "normalized.json"
        if normalized_path.is_file():
            payload = json.loads(normalized_path.read_text(encoding="utf-8"))
            if payload.get("provider") != self.provider or payload.get("source_pdf_sha256") != source_hash or payload.get("provenance", {}).get("config_hash") != self.config_hash:
                raise RuntimeError("cached live OCR artifact provenance mismatch")
            return normalized_path, payload

        pages_path = root / "pages.json"
        if pages_path.is_file():
            envelope = json.loads(pages_path.read_text(encoding="utf-8"))
            if envelope.get("source_pdf_sha256") != source_hash or envelope.get("ocr_config_hash") != self.ocr_config_hash:
                raise RuntimeError("cached OCR pages provenance mismatch")
            pages = validate_pages(envelope.get("pages"), expected_count)
        else:
            raw = _cached_json(root, "raw_response.*.json")
            if raw is None:
                raw, reported_count = self.transcriber.request(source)
                if reported_count != expected_count:
                    raise OcrProviderError("OCR page count changed during provider request")
                raw_bytes = _json_bytes(raw)
                _write_once(root / f"raw_response.{_sha256(raw_bytes)}.json", raw_bytes)
            else:
                raw_bytes = _json_bytes(raw)
            try:
                pages = self.transcriber.parse(raw, expected_count)
            except (ValueError, OcrProviderError) as error:
                raise OcrProviderError(f"{self.provider} OCR response validation failed: {error}") from error
            ocr_provenance = self.transcriber.config
            envelope = {"document_id": document_id, "provider": self.provider, "source_pdf_sha256": source_hash, "ocr_config_hash": self.ocr_config_hash, "ocr_provenance": ocr_provenance, "raw_response_sha256": _sha256(raw_bytes), "pages": pages}
            _write_once(pages_path, _json_bytes(envelope))
        _write_once(root / "ocr.txt", pages_text(pages).encode("utf-8"))

        structured_path = structure_root / "structured.json"
        if structured_path.is_file():
            structured_envelope = json.loads(structured_path.read_text(encoding="utf-8"))
            if structured_envelope.get("source_pdf_sha256") != source_hash or structured_envelope.get("ocr_config_hash") != self.ocr_config_hash or structured_envelope.get("structure_config_hash") != self.structure_config_hash:
                raise RuntimeError("cached structured OCR provenance mismatch")
            structured = structured_envelope.get("structured")
            if not isinstance(structured, dict):
                raise RuntimeError("cached structured OCR output is invalid")
        else:
            structure_raw = _cached_json(structure_root, "structure_raw_response.*.json")
            if structure_raw is None:
                structure_raw = self.structurer.request(self.provider, pages)
                structure_raw_bytes = _json_bytes(structure_raw)
                _write_once(structure_root / f"structure_raw_response.{_sha256(structure_raw_bytes)}.json", structure_raw_bytes)
            try:
                structured = self.structurer.parse(structure_raw)
            except OcrProviderError as error:
                raise OcrProviderError(f"{self.provider} structured response validation failed: {error}") from error
            structured_envelope = {"document_id": document_id, "provider": self.provider, "source_pdf_sha256": source_hash, "ocr_config_hash": self.ocr_config_hash, "structure_config_hash": self.structure_config_hash, "structured": structured}
            _write_once(structured_path, _json_bytes(structured_envelope))
        payload = {
            "document_id": document_id,
            "provider": self.provider,
            "source_pdf_sha256": source_hash,
            "provenance": {**self.structurer.config, "config_hash": self.config_hash},
            "ocr_provenance": envelope["ocr_provenance"],
            "identity": structured.get("identity"),
            "pages": [{"page": row["page"], "text": row["text"]} for row in pages],
            "span_dispositions": structured.get("span_dispositions"),
            "facts": structured.get("facts"),
        }
        _write_once(normalized_path, _json_bytes(payload))
        return normalized_path, payload

    def artifact_paths(self, document_id: str) -> dict[str, Path]:
        source = self.sources[document_id]
        source_hash = _sha256_file(source)
        root = self._root(document_id, source_hash)
        structure_root = self._structure_root(document_id, source_hash)
        result = {"pages": root / "pages.json", "ocr_text": root / "ocr.txt", "structured": structure_root / "structured.json", "normalized": structure_root / "normalized.json"}
        raw = sorted(root.glob("raw_response.*.json"))
        structure_raw = sorted(structure_root.glob("structure_raw_response.*.json"))
        if raw:
            result["raw_response"] = raw[-1]
        if structure_raw:
            result["structure_raw_response"] = structure_raw[-1]
        return result
