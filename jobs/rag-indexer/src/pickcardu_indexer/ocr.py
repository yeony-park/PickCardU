from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .grounding import RELATION_FIELDS, STRUCTURE_SCHEMA_VERSION, NORMALIZATION_CONTRACT, normalise_fact


LUNA_OCR_MODEL = "gpt-5.6-luna"
LUNA_REASONING = "max"
STRUCTURE_MODEL = "gpt-5.6-luna"
STRUCTURE_REASONING = "max"
UPSTAGE_ENDPOINT = "https://api.upstage.ai/v1/document-digitization"
UPSTAGE_MODEL = "document-parse"
MAX_MODEL_OUTPUT_TOKENS = 128_000
UPSTAGE_EMPTY_PAGE_POLICY = "pdf-structural-blank-v2"
UPSTAGE_BLANK_DOMINANT_COLOR_RATIO = 0.985
UPSTAGE_DECORATIVE_BACKGROUND_MIN_RATIO = 0.75
PROVIDER_MAX_ATTEMPTS = 2
LUNA_PAGE_FALLBACK_DPI = 200

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
                "required": ["page_num", "status", "markdown", "uncertain_spans"],
                "properties": {
                    "page_num": {"type": "integer", "minimum": 1},
                    "status": {"type": "string", "enum": ["success", "failed"]},
                    "markdown": {"type": "string"},
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

_FACT_PROPERTIES = {field: {"type": "string"} for field in RELATION_FIELDS}
_LINE_EVIDENCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["line_ids"],
    "properties": {
        "line_ids": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
}
_FIELD_FRAGMENT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["line_id", "fragment", "char_start", "char_end"],
    "properties": {
        "line_id": {"type": "string"},
        "fragment": {"type": "string"},
        "char_start": {"type": "integer", "minimum": 0},
        "char_end": {"type": "integer", "minimum": 1},
    },
}
_FIELD_EVIDENCE = {
    "type": "object",
    "additionalProperties": False,
    "required": list(RELATION_FIELDS),
    "properties": {field: {"type": "array", "items": _FIELD_FRAGMENT} for field in RELATION_FIELDS},
}
_RELATION_SCOPE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scope_type", "line_ids", "header_line_ids", "row_line_ids"],
    "properties": {
        "scope_type": {"type": "string", "enum": ["sentence", "bounded_block", "table_row"]},
        "line_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "header_line_ids": {"type": "array", "items": {"type": "string"}},
        "row_line_ids": {"type": "array", "items": {"type": "string"}},
    },
}
STRUCTURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["structure_schema_version", "identity", "facts", "ignored_risky_lines"],
    "properties": {
        "structure_schema_version": {"type": "string", "const": STRUCTURE_SCHEMA_VERSION},
        "identity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["issuer_name", "card_name", "issuer_evidence", "card_evidence"],
            "properties": {
                "issuer_name": {"type": "string"},
                "card_name": {"type": "string"},
                "issuer_evidence": _LINE_EVIDENCE,
                "card_evidence": _LINE_EVIDENCE,
            },
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [*_FACT_PROPERTIES, "field_evidence", "relation_scope"],
                "properties": {
                    **_FACT_PROPERTIES,
                    "field_evidence": _FIELD_EVIDENCE,
                    "relation_scope": _RELATION_SCOPE,
                },
            },
        },
        "ignored_risky_lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["line_id", "reason"],
                "properties": {
                    "line_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

OCR_PROMPT = """원본 카드 상품안내서의 내용을 정확하게 전사하세요.

목표:
- 문서에 실제로 보이는 문자만 전사하세요.
- 내용을 요약하거나 설명하지 마세요.
- 맞춤법, 띄어쓰기, 숫자, 상품명과 오탈자를 임의로 교정하지 마세요.
- 문맥상 그럴듯한 표현으로 추측하거나 원문에 없는 값을 만들지 마세요.

전사 규칙:
1. 제목, 본문, 목록, 각주와 표를 원문의 읽기 순서대로 기록하세요.
2. 숫자, 단위, 기호와 글머리표를 원문 그대로 보존하세요.
3. 표는 Markdown 표로 변환하세요.
4. 병합된 셀은 적용되는 각 열에 값을 반복하세요.
5. 한 셀 안의 줄바꿈은 ` / `로 표시하세요.
6. 로고는 식별 가능한 브랜드명만 기록하세요.
7. 판독할 수 없는 문자는 추측하지 말고 �로 기록하고 uncertain_spans에 이유를 남기세요.
8. 이미지에 없는 Markdown 제목이나 설명을 추가하지 마세요.
9. 표지, 뒷표지, 로고만 있는 페이지와 빈 페이지도 pages 배열에 포함하세요.
10. 각 페이지를 완전히 전사했으면 status를 success로, 처리하지 못했으면 failed로 기록하세요.

모든 페이지를 빠짐없이 page_num 순서로 반환하고, 누락이나 중복이 없는지 확인하세요.
해설이나 코드 블록 없이 지정된 JSON 형식만 반환하세요."""

OCR_PAGE_FALLBACK_PROMPT = """이 이미지는 원본 카드 상품안내서에서 별도로 렌더링한 단일 페이지입니다.
이미지에 실제로 보이는 문자만 정확하게 전사하고 요약, 교정 또는 추측하지 마세요.
표는 Markdown 표로 보존하고 숫자, 단위, 기호와 읽기 순서를 유지하세요.
로고만 있는 페이지도 식별 가능한 브랜드명을 markdown에 기록하고 status를 success로 반환하세요.
완전히 빈 페이지도 markdown을 빈 문자열로 두고 status를 success로 반환하세요.
이미지 자체를 확인할 수 없을 때만 status를 failed로 반환하세요.
pages 배열에는 지정된 page_num의 항목 하나만 반환하세요."""

STRUCTURE_PROMPT = """주어진 단일 OCR lane에서 카드 혜택과 적용 조건을 원문 근거에 연결해 구조화하세요. 다른 OCR 결과, 사전 지식, 아래 설명용 예시의 값을 입력 문서에 보충하지 마세요. 지정된 JSON 스키마만 반환하세요.

1. 원문 보존과 공통 형식
structure_schema_version은 field-evidence-relation-v6입니다. issuer_name과 card_name은 각각 identity.issuer_evidence.line_ids와 identity.card_evidence.line_ids가 가리키는 원문의 표기와 띄어쓰기를 그대로 복사하세요. OCR 오탈자, 숫자, 단위, 조건과 예외를 교정·정규화·요약하지 마세요. 비교용 정규화는 후속 코드가 수행합니다.
모든 fact에는 benefit_type, action, target, condition, value, unit, cap, frequency, period, exceptions와 field_evidence, relation_scope를 넣으세요. 내용이 없는 문자열 필드는 빈 문자열, 해당 field_evidence는 빈 배열로 두세요. 임의 필드나 새 상태값을 만들지 마세요.

2. 필드의 의미
모든 필드 값은 아래 역할에 해당하는 원문 문구를 복사하세요. 역할 설명이나 예시 단어를 대신 채우지 마세요.
- benefit_type: 원문에 표시된 혜택·서비스 종류 또는 이름.
- action: 할인, 적립, 면제, 제공, 적용, 제외 등 원문이 명시한 행위나 상태. 제외를 제공으로 바꾸지 마세요.
- target: 해당 행위가 적용되는 가맹점·업종·서비스·수수료 등 대상. 다른 카드의 혜택을 현재 카드 자체 혜택으로 바꾸지 마세요.
- condition: 전월 실적, 결제 수단, 지정 카드 선택, 가입 자격 등 혜택이 성립하는 전제. 숫자가 없는 전제도 조건입니다.
- value와 unit: 할인율·적립률·금액 등 혜택 값과 그 값에 붙은 단위. 비수치 결과는 원문 결과 문구를 value에 보존하고 unit은 빈 문자열로 두세요. 면제를 임의로 0원으로 바꾸지 마세요.
- cap: 월·건별 등 할인/적립 한도와 그 적용 범위.
- frequency: 횟수 제한과 그 기준.
- period: 적용·유효 기간이나 시점.
- exceptions: 제외 대상과 예외 조건. 원문에서 조건과 예외를 분리할 수 없으면 condition에 전체 문구를 보존하세요. 중요한 예외를 빠뜨리거나 긍정 조건으로 바꾸지 마세요.

3. 혜택과 조건을 함께 추출
혜택 결과 문장만 보고 condition을 비워 두지 마세요. 같은 관계에 명확히 연결된 앞뒤 문장·공통 제목·표 머리글·주석의 전제, 한도, 횟수, 기간, 예외를 함께 확인하세요. 실제로 연결되는 조건은 보존하되, 가깝다는 이유만으로 다른 혜택의 조건을 가져오지 마세요.
일반 문장의 condition, cap, frequency, period, exceptions는 실적·한도·횟수·기간·제외 등 의미를 나타내는 문구와 관련 수치·단위를 함께 보존하세요. 표에서는 머리글의 의미에 맞는 해당 행·열의 셀 전체를 보존하세요. 머리글을 셀 값으로 복사하지 마세요. value와 unit은 합쳐서 원문 값 전체를 보존해야 하며, unit을 다른 조건이나 한도에서 빌리지 마세요.
같은 문서에 실적 구간이나 한도가 여러 개이면 각 구간과 대응 값의 짝을 유지하세요. 서로 다른 구간의 조건 목록과 한도 목록을 따로 모아 짝을 알 수 없게 만들지 마세요. 독립적인 혜택·구간·대상은 각각의 관계로 구분하고 중요한 수치나 적용 조건을 생략하지 마세요.

4. 필드별 원문 근거
입력의 각 OCR 줄에는 고유한 line_id가 있습니다. identity 근거는 identity.issuer_evidence.line_ids와 identity.card_evidence.line_ids를 사용하세요. 각 field_evidence[field] 배열의 fragment에는 line_id, 원문 fragment, 정확한 0-based char_start와 exclusive char_end를 넣으세요. 좌표는 입력 text 자체를 기준으로 세며 앞의 공백·Markdown 기호를 포함한 원문 줄을 바꾸지 마세요. text[char_start:char_end]가 fragment와 정확히 같아야 합니다. 반복되는 같은 문구는 실제로 선택한 위치를 지정하세요.
같은 field의 fragment를 원문 순서대로 공백으로 이으면 해당 field 값과 같아야 합니다. 같은 field의 fragment 사이에서 공백 이외의 원문을 건너뛰지 마세요. 각 fragment의 줄은 relation_scope.line_ids 안에 있어야 합니다. 근거를 맞추려고 다른 문구나 넓은 범위를 추가하지 마세요.

5. 하나의 혜택 관계를 입증하는 범위
relation_scope는 sentence(한 원문 줄), bounded_block(명확히 하나의 관계를 담은 연속 문단), table_row(표 머리글과 단일 데이터 행) 중 하나입니다.
sentence와 bounded_block의 header_line_ids, row_line_ids는 빈 배열입니다. table_row에는 정확히 해당 표의 머리글 한 줄과 데이터 행 한 줄을 각각 지정하고 line_ids에도 포함하세요. Markdown 구분선은 데이터 행이나 근거로 지정하지 마세요. 같은 표에 바로 붙고 해당 행에 적용되는 공통 제목·주석은 line_ids에 함께 포함할 수 있습니다.
공통 제목의 적용 범위는 원문의 계층·문장 연결·표 구조로 확인하세요. 다른 동급 섹션이나 다른 표의 조건을 합치지 마세요. 제목, 혜택 값, 조건과 예외가 여러 줄에 나뉘어도 명확히 같은 혜택 블록이면 하나의 fact로 묶고 사용한 모든 근거 줄을 포함하세요. 단순한 근접성만으로 불명확한 관계를 확정하지 마세요.

6. 설명용 가상 예시 — 입력 문서에 복사하지 마세요
- 원문: '편의점 8% 할인, 전월 실적 25만원 이상, 월 할인 한도 4천원'. target='편의점', action='할인', value='8', unit='%', condition='전월 실적 25만원 이상', cap='월 할인 한도 4천원'처럼 수치 역할을 구분합니다. cap의 4천원을 value로 옮기면 잘못입니다.
- 원문: '지정 결제수단을 선택한 경우'와 그 결과인 '문화시설 할인 적용'이 명확히 연결됨. 결과만 추출해 condition을 비우면 잘못입니다. 조건과 결과의 원문 근거를 함께 보존합니다.
- 원문 공통 제목: '일상 할인 6%', 그 아래 '서점', '제과점', 다음 동급 제목: '여행 적립 2%'. 제목의 적용이 명확할 때 서점·제과점 각각에 6%를 연결하며, 여행의 2%와 섞지 않습니다. 공통 제목만 독립 혜택으로 만들지 않습니다.
- 원문 표: '실적 | 월 한도' 아래 '25만원 | 4천원', '45만원 | 9천원'. 25만원과 4천원, 45만원과 9천원의 짝을 각각 보존합니다. 다른 행이나 한도 열의 값을 바꿔 붙이지 않습니다.
- 원문: '연회비 없음. 별도로 보유한 카드의 연회비는 청구됨'. 없음과 별도 카드 예외를 보존합니다. 모든 카드의 연회비가 없다고 확대하지 않습니다.
예시는 일부 필드의 의미 설명이며 실제 응답에서는 전체 필드와 실제 입력의 근거를 반환하세요.

7. 누락과 추측 방지
모든 fact에는 원문으로 뒷받침되는 비어 있지 않은 benefit_type, action, target과 condition/value/cap/frequency/period/exceptions 중 하나 이상이 있어야 합니다. 숫자만 있는 줄로 관계를 만들지 마세요. 표현을 새로 만들어 필수 필드를 채우지 마세요.
할인, 적립, 캐시백, 마일, 포인트, 무료, 면제, 혜택 표현이 있는 줄은 관련 fact의 field_evidence와 relation_scope로 연결하세요. 법정·일반 안내나 표 머리글이라 fact에 포함하지 않는 경우에만 ignored_risky_lines에 line_id와 구체적인 reason을 기록하세요. 불명확한 실제 혜택을 일반 안내로 표시해 숨기지 마세요. 근거로 관계를 입증할 수 없으면 추측한 fact를 만들지 마세요. 미추출 위험 줄의 판단은 후속 검증에 맡기며 그 밖의 미사용 줄은 출력하지 마세요."""


def numbered_ocr_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "page": row["page"],
            "lines": [
                {"line_id": f"P{row['page']:04d}-L{line_number:04d}", "text": line}
                for line_number, line in enumerate(row["text"].splitlines(), start=1)
                if line.strip()
            ],
        }
        for row in pages
    ]


class OcrProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class PageFallbackError(OcrProviderError):
    pass


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


def _retryable_openai_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status in {408, 409, 429} or isinstance(status, int) and status >= 500:
        return True
    try:
        from openai import APIConnectionError
    except ImportError:
        return isinstance(error, (TimeoutError, ConnectionError))
    return isinstance(error, (TimeoutError, ConnectionError, APIConnectionError))


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
        status = row.get("status", "success")
        uncertain = row.get("uncertain_spans", [])
        is_blank = row.get("is_blank", False)
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or not isinstance(text, str)
            or status not in {"success", "failed"}
            or not isinstance(uncertain, list)
            or not isinstance(is_blank, bool)
        ):
            raise ValueError("OCR page fields are invalid")
        if status != "success":
            raise ValueError(f"OCR page {page} has failed status")
        if any(
            not isinstance(span, dict)
            or not isinstance(span.get("text"), str)
            or not isinstance(span.get("reason"), str)
            for span in uncertain
        ):
            raise ValueError(f"OCR page {page} has invalid uncertain_spans")
        normalized_row = dict(row)
        normalized_row.pop("page_num", None)
        normalized_row.pop("markdown", None)
        normalized_row.update({"page": page, "text": text, "status": status, "is_blank": is_blank, "uncertain_spans": uncertain})
        pages.append(normalized_row)
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


def _upstage_elements(raw: dict[str, Any]) -> list[dict[str, Any]]:
    elements = raw.get("elements")
    if not isinstance(elements, list) and isinstance(raw.get("content"), dict):
        elements = raw["content"].get("elements")
    return elements if isinstance(elements, list) else []


def _bbox(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict) and "x" in value and "y" in value:
        x, y = float(value["x"]), float(value["y"])
        return {"x1": x, "y1": y, "x2": x, "y2": y}
    if isinstance(value, dict):
        for key in ("points", "vertices", "coordinates"):
            if key in value:
                return _bbox(value[key])
    if isinstance(value, list):
        points = [point for point in value if isinstance(point, dict) and "x" in point and "y" in point]
        if points:
            xs, ys = [float(point["x"]) for point in points], [float(point["y"]) for point in points]
            return {"x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys)}
    return None


def _coordinate_space(blocks: list[dict[str, Any]]) -> str:
    boxes = [block["bbox"] for block in blocks if block.get("bbox") is not None]
    if boxes and all(0.0 <= box[key] <= 1.01 for box in boxes for key in ("x1", "y1", "x2", "y2")):
        return "normalized_0_1"
    return "provider_coordinates"


def visually_blank_pages(
    source: Path,
    candidates: set[int],
    *,
    dominant_color_ratio: float = UPSTAGE_BLANK_DOMINANT_COLOR_RATIO,
    decorative_background_min_ratio: float = UPSTAGE_DECORATIVE_BACKGROUND_MIN_RATIO,
) -> set[int]:
    import pymupdf

    blank: set[int] = set()
    with pymupdf.open(source) as document:
        for page_number in sorted(candidates):
            if page_number < 1 or page_number > document.page_count:
                continue
            page = document[page_number - 1]
            native_text = re.sub(r"\s+", "", page.get_text("text"))
            if native_text and not native_text.isdigit():
                continue
            if page.get_images(full=True):
                continue
            annotations = page.annots()
            if annotations is not None and next(annotations, None) is not None:
                continue
            widgets = page.widgets()
            if widgets is not None and next(widgets, None) is not None:
                continue
            drawings = page.get_drawings()
            if drawings:
                fills = [drawing for drawing in drawings if drawing.get("type") == "f"]
                if len(drawings) > 3 or len(fills) != 1 or any(drawing.get("type") not in {"f", "s"} for drawing in drawings):
                    continue
                drawing = fills[0]
                items = drawing.get("items", [])
                if len(items) != 1 or items[0][0] != "re":
                    continue
                page_area = page.rect.get_area()
                covered_area = (items[0][1] & page.rect).get_area()
                if page_area and covered_area / page_area >= decorative_background_min_ratio:
                    blank.add(page_number)
                continue
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), colorspace=pymupdf.csGRAY, alpha=False)
            top_ratio, _color = pixmap.color_topusage()
            if top_ratio >= dominant_color_ratio:
                blank.add(page_number)
    return blank


def upstage_pages(raw: dict[str, Any], expected_count: int, *, allowed_empty_pages: set[int] | None = None) -> list[dict[str, Any]]:
    elements = _upstage_elements(raw)
    usage = raw.get("usage")
    if isinstance(usage, dict) and usage.get("pages") != expected_count:
        raise ValueError("Upstage reported page count does not match the source PDF")
    grouped = {page: {"blocks": [], "tables": []} for page in range(1, expected_count + 1)}
    for reading_order, element in enumerate(elements, start=1):
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
        text, block_type = _element_text(element), str(element.get("category") or element.get("type") or "unknown").lower()
        box = _bbox(element.get("coordinates") or element.get("bounding_box") or element.get("bbox"))
        block = {
            "block_id": f"p{page}_b{len(grouped[page]['blocks']) + 1:04d}",
            "reading_order": reading_order,
            "type": block_type,
            "text": text,
            "bbox": box,
            "source_id": element.get("id"),
        }
        if block_type == "table":
            table_id = str(element.get("id") or f"p{page}_t{len(grouped[page]['tables']) + 1:03d}")
            block["table_id"] = table_id
            grouped[page]["tables"].append({"table_id": table_id, "format": "markdown" if "|" in text else "html", "content": text, "bbox": box, "source_id": element.get("id")})
        grouped[page]["blocks"].append(block)
    if expected_count == 1 and not any(block["text"] for block in grouped[1]["blocks"]):
        content = raw.get("content")
        if isinstance(content, dict):
            fallback = str(content.get("markdown") or content.get("text") or "").strip()
            if fallback:
                grouped[1]["blocks"].append({"block_id": "p1_b0001", "reading_order": 1, "type": "unknown", "text": fallback, "bbox": None, "source_id": None})
    allowed_empty_pages = allowed_empty_pages or set()
    page_text = {page: "\n".join(block["text"] for block in value["blocks"] if block["text"]) for page, value in grouped.items()}
    empty_pages = [page for page, text in page_text.items() if not text and page not in allowed_empty_pages]
    if empty_pages:
        raise ValueError(f"Upstage response has no text for source pages: {empty_pages}")
    return [
        {
            "page": page,
            "text": page_text[page],
            "status": "success",
            "is_blank": not page_text[page] and page in allowed_empty_pages,
            "uncertain_spans": [],
            "blocks": value["blocks"],
            "tables": value["tables"],
            "coordinate_space": _coordinate_space(value["blocks"]),
        }
        for page, value in grouped.items()
    ]


class LunaOcrTranscriber:
    provider = "luna"
    validation_max_attempts = 2

    def __init__(
        self,
        api_key: str,
        *,
        model: str = LUNA_OCR_MODEL,
        reasoning: str = LUNA_REASONING,
        client: Any = None,
        max_attempts: int = PROVIDER_MAX_ATTEMPTS,
        sleeper: Any = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.api_key, self.model, self.reasoning, self._client = api_key, model, reasoning, client
        self.max_attempts, self._sleep = max_attempts, sleeper

    @property
    def config(self) -> dict[str, Any]:
        return {"endpoint": "openai.responses", "model": self.model, "reasoning": self.reasoning, "input": "pdf", "detail": "high", "prompt_sha256": _sha256(OCR_PROMPT.encode()), "schema_sha256": _sha256(_json_bytes(OCR_SCHEMA)), "output_limit": "provider_default", "max_attempts": self.max_attempts, "validation_max_attempts": self.validation_max_attempts}

    @property
    def page_fallback_config(self) -> dict[str, Any]:
        return {
            "input": "rendered_page_png",
            "dpi": LUNA_PAGE_FALLBACK_DPI,
            "detail": "high",
            "candidate_policy": "missing-failed-or-nonblank-empty-and-source-blank-v3",
            "prompt_sha256": _sha256(OCR_PAGE_FALLBACK_PROMPT.encode()),
            "schema_sha256": _sha256(_json_bytes(OCR_SCHEMA)),
        }

    def _client_instance(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, max_retries=0)
        return self._client

    def _request(self, content: list[dict[str, Any]], schema_name: str) -> dict[str, Any]:
        client = self._client_instance()
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = client.responses.create(
                    model=self.model,
                    reasoning={"effort": self.reasoning},
                    input=[{"role": "user", "content": content}],
                    text={"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": OCR_SCHEMA}},
                    store=False,
                    timeout=900.0,
                )
                return _response_dict(response)
            except Exception as error:
                last_error = error
                retryable = _retryable_openai_error(error)
                if not retryable or attempt == self.max_attempts:
                    raise OcrProviderError(f"Luna OCR failed: {type(error).__name__}: {error}", retryable=retryable) from error
                self._sleep(min(30.0, 2**attempt))
        raise OcrProviderError(f"Luna OCR failed: {last_error}", retryable=True)

    def request(self, source: Path) -> tuple[dict[str, Any], int]:
        count = pdf_page_count(source)
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        raw = self._request([
            {"type": "input_file", "filename": source.name, "file_data": f"data:application/pdf;base64,{encoded}", "detail": "high"},
            {"type": "input_text", "text": f"{OCR_PROMPT}\nPDF page count: {count}"},
        ], "ocr_pages")
        return raw, count

    def request_page(self, source: Path, page_number: int) -> dict[str, Any]:
        import pymupdf

        with pymupdf.open(source) as document:
            if page_number < 1 or page_number > document.page_count:
                raise ValueError("page fallback is outside the source PDF")
            png = document[page_number - 1].get_pixmap(dpi=LUNA_PAGE_FALLBACK_DPI, alpha=False).tobytes("png")
        encoded = base64.b64encode(png).decode("ascii")
        return self._request([
            {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "high"},
            {"type": "input_text", "text": f"{OCR_PAGE_FALLBACK_PROMPT}\nRequired page_num: {page_number}"},
        ], "ocr_page_fallback")

    def fallback_page_numbers(self, raw: dict[str, Any], expected_count: int, source: Path) -> list[int]:
        value = _output_json(None, raw).get("pages")
        if not isinstance(value, list):
            return []
        counts: dict[int, int] = {}
        failed: set[int] = set()
        empty: set[int] = set()
        for row in value:
            if not isinstance(row, dict):
                continue
            page = row.get("page_num", row.get("page"))
            if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > expected_count:
                continue
            counts[page] = counts.get(page, 0) + 1
            if row.get("status", "success") != "success":
                failed.add(page)
            text = row.get("markdown", row.get("text"))
            if isinstance(text, str) and not text.strip():
                empty.add(page)
        empty -= visually_blank_pages(source, empty)
        return sorted(failed | empty | {page for page in range(1, expected_count + 1) if counts.get(page) != 1})

    def parse_with_page_fallbacks(
        self,
        raw: dict[str, Any],
        fallback_raws: dict[int, dict[str, Any]],
        expected_count: int,
        source: Path,
    ) -> list[dict[str, Any]]:
        value = _output_json(None, raw).get("pages")
        if not isinstance(value, list):
            raise ValueError("OCR pages must be an array")
        recovered = set(fallback_raws)
        merged = [row for row in value if isinstance(row, dict) and row.get("page_num", row.get("page")) not in recovered]
        for page_number, fallback_raw in sorted(fallback_raws.items()):
            fallback_pages = _output_json(None, fallback_raw).get("pages")
            if not isinstance(fallback_pages, list) or len(fallback_pages) != 1:
                raise ValueError(f"Luna page fallback {page_number} must return exactly one page")
            row = fallback_pages[0]
            if not isinstance(row, dict) or row.get("page_num", row.get("page")) != page_number:
                raise ValueError(f"Luna page fallback returned the wrong page for {page_number}")
            merged.append(row)
        pages = validate_pages(merged, expected_count)
        empty_pages = {row["page"] for row in pages if not row["text"].strip()}
        blank_pages = visually_blank_pages(source, set(range(1, expected_count + 1)))
        unexpected = sorted(empty_pages - blank_pages)
        if unexpected:
            raise ValueError(f"Luna response has no text for source pages: {unexpected}")
        for page in pages:
            page["is_blank"] = page["page"] in blank_pages
            if page["is_blank"]:
                page["text"] = ""
            if page["page"] in recovered:
                page["recovered_from"] = f"rendered_page_png_{LUNA_PAGE_FALLBACK_DPI}dpi"
        return pages

    def parse(self, raw: dict[str, Any], expected_count: int) -> list[dict[str, Any]]:
        output = _output_json(None, raw)
        return validate_pages(output.get("pages"), expected_count)

    def parse_source(self, raw: dict[str, Any], expected_count: int, source: Path) -> list[dict[str, Any]]:
        pages = self.parse(raw, expected_count)
        empty_pages = {row["page"] for row in pages if not row["text"].strip()}
        blank_pages = visually_blank_pages(source, set(range(1, expected_count + 1)))
        unexpected = sorted(empty_pages - blank_pages)
        if unexpected:
            raise ValueError(f"Luna response has no text for source pages: {unexpected}")
        for page in pages:
            page["is_blank"] = page["page"] in blank_pages
            if page["is_blank"]:
                page["text"] = ""
        return pages

    def transcribe(self, source: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        raw, count = self.request(source)
        try:
            pages = self.parse_source(raw, count, source)
        except (ValueError, OcrProviderError):
            page_numbers = self.fallback_page_numbers(raw, count, source)
            if not page_numbers:
                raise
            fallback_raws = {page: self.request_page(source, page) for page in page_numbers}
            pages = self.parse_with_page_fallbacks(raw, fallback_raws, count, source)
        return raw, pages, {**self.config, "page_fallback": self.page_fallback_config}


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

    def __init__(self, api_key: str, *, opener: Any = None, max_attempts: int = PROVIDER_MAX_ATTEMPTS, sleeper: Any = time.sleep) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.api_key, self._opener = api_key, opener or urllib.request.urlopen
        self.max_attempts, self._sleep = max_attempts, sleeper

    @property
    def config(self) -> dict[str, Any]:
        return {"endpoint": UPSTAGE_ENDPOINT, "model": UPSTAGE_MODEL, "ocr": "force", "coordinates": True, "output_formats": ["html", "markdown"]}

    @property
    def parse_config(self) -> dict[str, Any]:
        return {
            "empty_page_policy": UPSTAGE_EMPTY_PAGE_POLICY,
            "dominant_color_ratio": UPSTAGE_BLANK_DOMINANT_COLOR_RATIO,
            "decorative_background_min_ratio": UPSTAGE_DECORATIVE_BACKGROUND_MIN_RATIO,
            "normalizer": "layout-blocks-v1",
            "blank_labeling": "empty-pages-only-v2",
            "max_attempts": self.max_attempts,
        }

    def request(self, source: Path) -> tuple[dict[str, Any], int]:
        count = pdf_page_count(source)
        body, boundary = _multipart_body(source)
        request = urllib.request.Request(UPSTAGE_ENDPOINT, data=body, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self._opener(request, timeout=900) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                retryable = error.code in {408, 429} or error.code >= 500
                if not retryable or attempt == self.max_attempts:
                    raise OcrProviderError(f"Upstage OCR HTTP {error.code}: {detail[-1000:]}", retryable=retryable) from error
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = float(retry_after) if retry_after is not None else 0.0
                except ValueError:
                    delay = 0.0
                self._sleep(max(delay, min(30.0, 2**attempt)))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if attempt == self.max_attempts:
                    raise OcrProviderError(f"Upstage OCR failed: {type(error).__name__}: {error}", retryable=True) from error
                self._sleep(min(30.0, 2**attempt))
            except Exception as error:
                raise OcrProviderError(f"Upstage OCR failed: {type(error).__name__}: {error}") from error
        if not isinstance(raw, dict):
            raise OcrProviderError("Upstage response must be an object")
        return raw, count

    def parse(self, raw: dict[str, Any], expected_count: int) -> list[dict[str, Any]]:
        return upstage_pages(raw, expected_count)

    def parse_source(self, raw: dict[str, Any], expected_count: int, source: Path) -> list[dict[str, Any]]:
        pages = upstage_pages(raw, expected_count, allowed_empty_pages=set(range(1, expected_count + 1)))
        empty_pages = {row["page"] for row in pages if not row["text"].strip()}
        allowed_empty_pages = visually_blank_pages(source, empty_pages)
        unexpected = sorted(empty_pages - allowed_empty_pages)
        if unexpected:
            raise ValueError(f"Upstage response has no text for source pages: {unexpected}")
        return pages

    def transcribe(self, source: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        raw, count = self.request(source)
        return raw, self.parse_source(raw, count, source), self.config


class LunaFactStructurer:
    def __init__(self, api_key: str, *, model: str = STRUCTURE_MODEL, reasoning: str = STRUCTURE_REASONING, client: Any = None) -> None:
        self.api_key, self.model, self.reasoning, self._client = api_key, model, reasoning, client

    @property
    def config(self) -> dict[str, Any]:
        return {"endpoint": "openai.responses", "model": self.model, "reasoning": self.reasoning, "prompt_sha256": _sha256(STRUCTURE_PROMPT.encode()), "schema_sha256": _sha256(_json_bytes(STRUCTURE_SCHEMA)), "max_output_tokens": MAX_MODEL_OUTPUT_TOKENS}

    def request(self, provider: str, pages: list[dict[str, Any]]) -> dict[str, Any]:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, max_retries=0)
        source = json.dumps(numbered_ocr_pages(pages), ensure_ascii=False)
        try:
            response = self._client.responses.create(
                model=self.model,
                reasoning={"effort": self.reasoning},
                input=[{"role": "user", "content": [{"type": "input_text", "text": f"{STRUCTURE_PROMPT}\nLane: {provider}\nOCR pages JSON:\n{source}"}]}],
                text={"format": {"type": "json_schema", "name": "card_facts", "strict": True, "schema": STRUCTURE_SCHEMA}},
                store=False,
                max_output_tokens=MAX_MODEL_OUTPUT_TOKENS,
                timeout=1800.0,
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
        self.parse_config = getattr(transcriber, "parse_config", {})
        self.parse_config_hash = _sha256(_json_bytes(self.parse_config)) if self.parse_config else None
        self.page_fallback_config = getattr(transcriber, "page_fallback_config", {})
        self.page_fallback_config_hash = _sha256(_json_bytes(self.page_fallback_config)) if self.page_fallback_config else None
        self.structure_config_hash = _sha256(_json_bytes(structurer.config))
        config = {"ocr": transcriber.config, "structure": structurer.config, "normalization": NORMALIZATION_CONTRACT}
        if self.parse_config:
            config["ocr_parse"] = self.parse_config
        if self.page_fallback_config:
            config["page_fallback"] = self.page_fallback_config
        self.config_hash = _sha256(_json_bytes(config))

    def _root(self, document_id: str, source_hash: str) -> Path:
        return self.root / document_id.replace("/", "__") / self.provider / source_hash / self.ocr_config_hash

    def _pages_root(self, document_id: str, source_hash: str) -> Path:
        root = self._root(document_id, source_hash)
        if self.parse_config_hash:
            root = root / "parse" / self.parse_config_hash
        if self.page_fallback_config_hash:
            root = root / "page-fallback" / self.page_fallback_config_hash
        return root

    def _structure_root(self, document_id: str, source_hash: str) -> Path:
        return self._pages_root(document_id, source_hash) / "structure" / self.structure_config_hash

    def _context(self, document_id: str) -> tuple[Path, str, int, Path, Path, Path]:
        source = self.sources[document_id]
        try:
            source_hash = _sha256_file(source)
            expected_count = pdf_page_count(source)
        except Exception as error:
            raise OcrProviderError(f"source PDF preflight failed: {type(error).__name__}: {error}") from error
        request_root = self._root(document_id, source_hash)
        pages_root = self._pages_root(document_id, source_hash)
        return source, source_hash, expected_count, request_root, pages_root, self._structure_root(document_id, source_hash)

    def read_extracted(self, document_id: str) -> tuple[str, list[dict[str, Any]], dict[str, Any], Path, Path]:
        _source, source_hash, expected_count, _request_root, pages_root, structure_root = self._context(document_id)
        pages_path = pages_root / "pages.json"
        if not pages_path.is_file():
            raise FileNotFoundError(f"{self.provider} OCR extraction is missing; run extract first")
        envelope = json.loads(pages_path.read_text(encoding="utf-8"))
        if envelope.get("source_pdf_sha256") != source_hash or envelope.get("ocr_config_hash") != self.ocr_config_hash or envelope.get("parse_config_hash") != self.parse_config_hash or envelope.get("page_fallback_config_hash") != self.page_fallback_config_hash:
            raise RuntimeError("cached OCR pages provenance mismatch")
        pages = validate_pages(envelope.get("pages"), expected_count)
        return source_hash, pages, envelope, pages_root, structure_root

    def _parse_with_page_fallback(
        self,
        raw: dict[str, Any],
        expected_count: int,
        source: Path,
        request_root: Path,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        parse_source = getattr(self.transcriber, "parse_source", None)
        try:
            pages = parse_source(raw, expected_count, source) if parse_source else self.transcriber.parse(raw, expected_count)
            return validate_pages(pages, expected_count), []
        except (ValueError, OcrProviderError):
            candidates = getattr(self.transcriber, "fallback_page_numbers", None)
            request_page = getattr(self.transcriber, "request_page", None)
            parse_fallbacks = getattr(self.transcriber, "parse_with_page_fallbacks", None)
            if not all(callable(item) for item in (candidates, request_page, parse_fallbacks)):
                raise
            page_numbers = candidates(raw, expected_count, source)
            if not page_numbers:
                raise
            fallback_raws: dict[int, dict[str, Any]] = {}
            metadata: list[dict[str, Any]] = []
            try:
                for page_number in page_numbers:
                    fallback_root = request_root / "page_fallbacks" / str(self.page_fallback_config_hash) / f"page-{page_number:04d}"
                    fallback_raw = _cached_json(fallback_root, "raw_response.*.json")
                    if fallback_raw is None:
                        fallback_raw = request_page(source, page_number)
                        fallback_bytes = _json_bytes(fallback_raw)
                        _write_once(fallback_root / f"raw_response.{_sha256(fallback_bytes)}.json", fallback_bytes)
                    else:
                        fallback_bytes = _json_bytes(fallback_raw)
                    fallback_raws[page_number] = fallback_raw
                    metadata.append({
                        "page": page_number,
                        "raw_response_sha256": _sha256(fallback_bytes),
                        "provider_response_metadata": {key: fallback_raw[key] for key in ("id", "model", "usage") if key in fallback_raw},
                    })
                pages = parse_fallbacks(raw, fallback_raws, expected_count, source)
            except (ValueError, OcrProviderError) as error:
                raise PageFallbackError(f"{self.provider} page image fallback failed: {error}", retryable=getattr(error, "retryable", False)) from error
            return validate_pages(pages, expected_count), metadata

    def extract(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        source, source_hash, expected_count, request_root, pages_root, _structure_root = self._context(document_id)
        pages_path = pages_root / "pages.json"
        if pages_path.is_file():
            _source_hash, _pages, envelope, _pages_root, _structure_root = self.read_extracted(document_id)
            return pages_path, envelope
        raw = _cached_json(request_root, "raw_response.*.json")
        validation_attempts = 0
        page_fallbacks: list[dict[str, Any]] = []
        if raw is not None:
            raw_bytes = _json_bytes(raw)
            try:
                pages, page_fallbacks = self._parse_with_page_fallback(raw, expected_count, source, request_root)
            except (ValueError, OcrProviderError) as error:
                raise OcrProviderError(f"{self.provider} OCR response validation failed: {error}") from error
        else:
            max_attempts = max(1, int(getattr(self.transcriber, "validation_max_attempts", 1)))
            for validation_attempts in range(1, max_attempts + 1):
                raw, reported_count = self.transcriber.request(source)
                raw_bytes = _json_bytes(raw)
                try:
                    if reported_count != expected_count:
                        raise ValueError("OCR page count changed during provider request")
                    pages, page_fallbacks = self._parse_with_page_fallback(raw, expected_count, source, request_root)
                except PageFallbackError:
                    _write_once(request_root / f"raw_response.{_sha256(raw_bytes)}.json", raw_bytes)
                    raise
                except (ValueError, OcrProviderError) as error:
                    failure_root = request_root / "failed_attempts" / f"attempt-{validation_attempts:02d}"
                    _write_once(failure_root / f"raw_response.{_sha256(raw_bytes)}.json", raw_bytes)
                    _write_once(failure_root / "error.json", _json_bytes({"error": str(error)}))
                    if validation_attempts < max_attempts:
                        continue
                    _write_once(request_root / f"raw_response.{_sha256(raw_bytes)}.json", raw_bytes)
                    raise OcrProviderError(f"{self.provider} OCR response validation failed: {error}") from error
                _write_once(request_root / f"raw_response.{_sha256(raw_bytes)}.json", raw_bytes)
                break
        response_metadata = {key: raw[key] for key in ("id", "api", "model", "usage") if key in raw}
        envelope = {"document_id": document_id, "provider": self.provider, "source_pdf_sha256": source_hash, "ocr_config_hash": self.ocr_config_hash, "parse_config_hash": self.parse_config_hash, "page_fallback_config_hash": self.page_fallback_config_hash, "ocr_provenance": self.transcriber.config, "ocr_parse_provenance": self.parse_config, "page_fallback_provenance": self.page_fallback_config, "raw_response_sha256": _sha256(raw_bytes), "provider_response_metadata": response_metadata, "page_fallbacks": page_fallbacks, "pages": pages}
        if validation_attempts:
            envelope["validation_attempts"] = validation_attempts
        _write_once(pages_path, _json_bytes(envelope))
        _write_once(pages_root / "ocr.txt", pages_text(pages).encode("utf-8"))
        return pages_path, envelope

    def structure(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        source_hash, pages, _envelope, _root, structure_root = self.read_extracted(document_id)
        structured_path = structure_root / "structured.json"
        if structured_path.is_file():
            structured_envelope = json.loads(structured_path.read_text(encoding="utf-8"))
            if structured_envelope.get("source_pdf_sha256") != source_hash or structured_envelope.get("ocr_config_hash") != self.ocr_config_hash or structured_envelope.get("parse_config_hash") != self.parse_config_hash or structured_envelope.get("structure_config_hash") != self.structure_config_hash:
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
            structured_envelope = {"document_id": document_id, "provider": self.provider, "source_pdf_sha256": source_hash, "ocr_config_hash": self.ocr_config_hash, "parse_config_hash": self.parse_config_hash, "structure_config_hash": self.structure_config_hash, "structured": structured}
            _write_once(structured_path, _json_bytes(structured_envelope))
        return structured_path, structured_envelope

    def read_structured(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        source_hash, _pages, _envelope, _root, structure_root = self.read_extracted(document_id)
        structured_path = structure_root / "structured.json"
        if not structured_path.is_file():
            raise FileNotFoundError(f"{self.provider} structure is missing; run structure first")
        structured_envelope = json.loads(structured_path.read_text(encoding="utf-8"))
        if structured_envelope.get("source_pdf_sha256") != source_hash or structured_envelope.get("ocr_config_hash") != self.ocr_config_hash or structured_envelope.get("parse_config_hash") != self.parse_config_hash or structured_envelope.get("structure_config_hash") != self.structure_config_hash:
            raise RuntimeError("cached structured OCR provenance mismatch")
        if not isinstance(structured_envelope.get("structured"), dict):
            raise RuntimeError("cached structured OCR output is invalid")
        return structured_path, structured_envelope

    def normalize(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        source_hash, pages, envelope, _root, structure_root = self.read_extracted(document_id)
        _structured_path, structured_envelope = self.read_structured(document_id)
        structured = structured_envelope.get("structured")
        normalized_path = structure_root / "normalization" / NORMALIZATION_CONTRACT / "normalized.json"
        raw_facts = structured.get("facts")
        normalized_facts: list[Any] = []
        normalization_errors: list[dict[str, Any]] = []
        if isinstance(raw_facts, list):
            for ordinal, raw_fact in enumerate(raw_facts):
                if not isinstance(raw_fact, dict):
                    normalized_facts.append(raw_fact)
                    normalization_errors.append({"fact_index": ordinal, "error": "fact is not an object"})
                    continue
                preserved = dict(raw_fact)
                try:
                    preserved["typed_normalization"] = normalise_fact(raw_fact)["typed_normalization"]
                except ValueError as error:
                    normalization_errors.append({"fact_index": ordinal, "error": str(error)})
                normalized_facts.append(preserved)
        else:
            normalized_facts = raw_facts
            normalization_errors.append({"fact_index": None, "error": "facts is not an array"})
        payload = {
            "document_id": document_id,
            "provider": self.provider,
            "source_pdf_sha256": source_hash,
            "provenance": {**self.structurer.config, "config_hash": self.config_hash},
            "ocr_provenance": envelope["ocr_provenance"],
            "ocr_parse_provenance": envelope["ocr_parse_provenance"],
            "structure_schema_version": structured.get("structure_schema_version"),
            "identity": structured.get("identity"),
            "pages": [dict(row) for row in pages],
            # Preserve provider field strings and source evidence byte-for-byte;
            # typed values are local comparison metadata, not provider output.
            "facts": normalized_facts,
            "normalization_errors": normalization_errors,
            "normalization_contract": NORMALIZATION_CONTRACT,
        }
        if "span_dispositions" in structured:
            payload["span_dispositions"] = structured["span_dispositions"]
        if "ignored_risky_lines" in structured:
            payload["ignored_risky_lines"] = structured["ignored_risky_lines"]
        _write_once(normalized_path, _json_bytes(payload))
        return normalized_path, payload

    def read_normalized(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        _source, source_hash, _expected_count, _request_root, _pages_root, structure_root = self._context(document_id)
        normalized_path = structure_root / "normalization" / NORMALIZATION_CONTRACT / "normalized.json"
        if not normalized_path.is_file():
            raise FileNotFoundError(f"{self.provider} normalized JSON is missing; run normalize first")
        payload = json.loads(normalized_path.read_text(encoding="utf-8"))
        if payload.get("provider") != self.provider or payload.get("source_pdf_sha256") != source_hash or payload.get("provenance", {}).get("config_hash") != self.config_hash or payload.get("normalization_contract") != NORMALIZATION_CONTRACT:
            raise RuntimeError("cached live OCR artifact provenance mismatch")
        return normalized_path, payload

    def load(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        self.extract(document_id)
        self.structure(document_id)
        self.normalize(document_id)
        return self.read_normalized(document_id)

    def artifact_paths(self, document_id: str) -> dict[str, Path]:
        source = self.sources[document_id]
        source_hash = _sha256_file(source)
        request_root = self._root(document_id, source_hash)
        pages_root = self._pages_root(document_id, source_hash)
        structure_root = self._structure_root(document_id, source_hash)
        result = {"pages": pages_root / "pages.json", "ocr_text": pages_root / "ocr.txt", "structured": structure_root / "structured.json", "normalized": structure_root / "normalization" / NORMALIZATION_CONTRACT / "normalized.json"}
        raw = sorted(request_root.glob("raw_response.*.json"))
        structure_raw = sorted(structure_root.glob("structure_raw_response.*.json"))
        if raw:
            result["raw_response"] = raw[-1]
        if structure_raw:
            result["structure_raw_response"] = structure_raw[-1]
        for path in sorted(request_root.glob("failed_attempts/attempt-*/raw_response.*.json")):
            result[f"failed_raw_response_{path.parent.name}"] = path
        for path in sorted(request_root.glob("failed_attempts/attempt-*/error.json")):
            result[f"validation_error_{path.parent.name}"] = path
        fallback_pattern = f"page_fallbacks/{self.page_fallback_config_hash}/page-*/raw_response.*.json"
        for path in sorted(request_root.glob(fallback_pattern)) if self.page_fallback_config_hash else []:
            result[f"page_fallback_{path.parent.name}"] = path
        return result
