from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import fcntl
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

import numpy as np
from pickcardu_rag.retrieval import LEXICAL_CONTRACT, lexical_terms

from .state import StateStore, canonical_json
from .ocr import LiveLaneAdapter, OcrProviderError, numbered_ocr_pages, pages_text
from .grounding import (
    CHUNKING_CONTRACT,
    RELATION_FIELDS,
    STRUCTURE_SCHEMA_VERSION,
    normalise_fact as _normalise_fact,
    normalized as _grounded_normalized,
    relation_key,
    typed_literals,
    condition_operators,
    field_comparison_key,
    fact_bundles,
    BUNDLE_FIELDS,
    literal_number_spans,
    material_text_key,
)
from .structural import HEADING_RE, STRUCTURAL_CONTRACT, build_structural_chunks, render_pages


NUMBER = re.compile(r"\d+(?:[,.]\d+)?")
NUMBER_TOKEN = re.compile(r"(?<![\d.])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])")
SOURCE_UNIT = re.compile(r"\s*(?:[조억만천백십]\s*원|원|%|％|퍼센트|마일리지|마일|포인트|개월|회|년|일|시간|분|초|점|달러)")
NAMED_MEASURE_UNIT = re.compile(
    r"(?:[%％]\s*)?(?:[A-Za-z][A-Za-z0-9]*(?:\s+리워드)?\s*)?(?:포인트|마일리지|마일)|명"
)
CLOCK_LITERAL = re.compile(r"(?<![\d:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])|(?<!\d)(?:(?:오전|오후)\s*(?:0?[1-9]|1[0-2])|(?:[01]?\d|2[0-3]))\s*시(?:\s*[0-5]?\d\s*분)?(?!간)")
DATE_LITERAL = re.compile(r"(?<![\d-])(\d{4})(?:\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일|-(\d{2})-(\d{2}))(?![\d-])")
PHONE_LITERAL = re.compile(r"(?<![\d-])(?:0\d{1,2}-\d{3,4}-\d{4}|1\d{3}-\d{4})(?![\d-])")
CONTACT_LABEL = re.compile(r"☎|전화|고객센터|고객상담|문의|연락처|팩스")
RISKY_IGNORED_LINE = re.compile(r"할인|적립|캐시백|마일|포인트|무료|면제|혜택")
BENEFIT_VERB_SIGNAL = re.compile(r"할인|적립|캐시백|무료|면제")
BENEFIT_NOUN_SUFFIX = re.compile(r"\s*(?:율|률|대상|제외(?:\s*대상)?)")
BENEFIT_COMBINED_LABEL = re.compile(r"(?:할인/적립|적립/할인)")
CRITICAL_RELATION_LINE = re.compile(r"할인|적립|캐시백|마일|포인트|무료|면제|(?:전월|지난달).?실적|이용.?금액|한도|횟수|이상|초과|이하|미만|제외|불가|조건")
FIELD_ROLE_MARKERS = {
    "condition": re.compile(r"(?:전월|지난달).?실적|이용.?금액|이상|초과|이하|미만|조건"),
    "cap": re.compile(r"한도"),
    "frequency": re.compile(r"횟수|월\s*\d+회|일\s*\d+회|연\s*\d+회"),
    "period": re.compile(r"기간|개월|연간|월간"),
    "exceptions": re.compile(r"제외|불가|미적용"),
}
NAMED_TABLE_NOTE = re.compile(r"^[\s※•·*#-]*(.+?)\s*(청구할인|할인|적립)\s*(제외\s*대상|대상|조건|한도|제외)\s*[:：]\s*(.+)")
EXPLICIT_CAP = re.compile(r"(?<![가-힣\d])(?:월|연|일|건당|건)\s*(?:최대|할인한도|적립한도|한도)\s*[-+]?\d[\d,.]*(?:\s*[조억만천백십]\s*\d[\d,.]*)*\s*[조억만천백십]?\s*원")
TABLE_FIELD_ROLE = {
    "target": "target",
    "value": "value",
    "unit": "value",
    "condition": "condition",
    "cap": "cap",
    "frequency": "frequency",
    "period": "period",
    "exceptions": "exceptions",
}
NON_BENEFIT_IGNORED_LINE = re.compile(r"연체|신용평점|카드 발급|카드 신규 출시|부가서비스.*(?:유지|변경|소요된 비용)")
LAYOUT_REASON = re.compile(r"제목|머리글|헤더|열(?:\s+)?제목")
MARKUP_PREFIX = re.compile(r"^(?:[#>*•-]+\s*)+")
EVIDENCE_FORMAT_PREFIX = re.compile(r"^(?:#{1,6}\s+|•\s*)")
GENERIC_LAYOUT_LABELS = {
    "구분", "대상", "서비스", "서비스 안내", "혜택", "혜택 안내", "할인", "할인 서비스",
    "할인율", "할인률", "적립", "적립 서비스", "적립율", "적립률", "업종", "영역",
    "가맹점", "조건", "한도", "전월실적", "기준", "부가서비스 안내", "청구할인 서비스",
}
GENERIC_LAYOUT_KEYS = {re.sub(r"\s+", "", label) for label in GENERIC_LAYOUT_LABELS}
CHUNKING_PROFILES = {"card_page_section_benefit", "parent_child_bundle"}
DEFAULT_CHUNKING_PROFILE = "card_page_section_benefit"
OCR_PIPELINE_CONTRACT = "dual-lane-claim-grounding-v28"


class LaneRestructureRequired(ValueError):
    pass


class LaneFactReview(ValueError):
    """Collect unresolved facts without making partially validated lanes usable."""

    def __init__(self, issues: list[dict[str, Any]], checked_count: int) -> None:
        self.issues = issues
        self.checked_count = checked_count
        super().__init__("; ".join(item["error"] for item in issues))

    def diagnostic(self) -> dict[str, Any]:
        return {"status": "review", "error": str(self), "issues": self.issues,
                "checked_fact_count": self.checked_count,
                "unresolved_fact_count": len(self.issues), "approval_eligible": False}


class CriticalContentMismatch(ValueError):
    """A grounded source fragment and its JSON value disagree on critical data."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_immutable(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != value:
            raise RuntimeError(f"immutable artifact changed: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def file_fingerprint(path: Path) -> str:
    try:
        return sha256_file(path)
    except OSError as error:
        return f"unreadable:{type(error).__name__}"


def tree_hash(root: Path) -> str:
    rows = [{"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)} for path in sorted(root.rglob("*")) if path.is_file()]
    return sha256_bytes(canonical_json(rows).encode())


def embedding_sha256(chunk_ids: list[str], embeddings: np.ndarray) -> str:
    array = np.asarray(embeddings, dtype=np.float32)
    if array.ndim != 2 or array.shape[0] != len(chunk_ids) or not np.isfinite(array).all():
        raise ValueError("embedding identity shape or finiteness mismatch")
    digest = hashlib.sha256(canonical_json(chunk_ids).encode())
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def input_fingerprint(source_manifest: Path, documents: list[dict[str, str]], luna_dir: Path | None, upstage_dir: Path | None) -> str:
    """Hashes every local input lane independently, so changed input invalidates resume."""
    lanes: dict[str, dict[str, str] | None] = {}
    for provider, root in (("luna", luna_dir), ("upstage", upstage_dir)):
        lanes[provider] = None if root is None else {
            document["document_id"]: sha256_file(lane_path(root, document["document_id"])) if lane_path(root, document["document_id"]).is_file() else "missing"
            for document in documents
        }
    return sha256_bytes(canonical_json({
        "manifest": sha256_file(source_manifest),
        "sources": {document["document_id"]: file_fingerprint(Path(document["source_pdf"])) for document in documents},
        "lanes": lanes,
    }).encode())


def normalized(value: object) -> str:
    return _grounded_normalized(value)


def approved_layout_ignore(line: str, reason: str) -> bool:
    if not LAYOUT_REASON.search(reason):
        return False
    content = MARKUP_PREFIX.sub("", normalized(line)).strip()
    if content in GENERIC_LAYOUT_LABELS:
        return True
    if content.startswith("|") and content.endswith("|"):
        cells = [cell.strip() for cell in content.strip("|").split("|") if cell.strip()]
        return len(cells) >= 2 and all(cell in GENERIC_LAYOUT_LABELS for cell in cells)
    return False


def _is_generic_layout_line(line: str) -> bool:
    content = MARKUP_PREFIX.sub("", normalized(line)).strip()
    return re.sub(r"\s+", "", content) in GENERIC_LAYOUT_KEYS


def relation_tuple(fact: dict[str, Any]) -> tuple[str, ...]:
    return relation_key(fact)


def normalise_fact(raw: dict[str, Any]) -> dict[str, Any]:
    """Public compatibility entrypoint for v6 typed raw relation normalization."""
    return _normalise_fact(raw)


def numbers(value: str) -> set[str]:
    return {token.replace(",", "") for token in NUMBER.findall(value)}


def _critical_source_line(line: str) -> bool:
    """Include data rows whose benefit/action is expressed in their header."""
    return bool(CRITICAL_RELATION_LINE.search(line) or RISKY_IGNORED_LINE.search(line)
                or line.lstrip().startswith("|") and NUMBER_TOKEN.search(line))


def evidence_pages(value: Any) -> list[int]:
    pages: set[int] = set()
    if isinstance(value, dict):
        page = value.get("page")
        if isinstance(page, int) and not isinstance(page, bool) and page >= 1:
            pages.add(page)
        for nested in value.values():
            pages.update(evidence_pages(nested))
    elif isinstance(value, list):
        for nested in value:
            pages.update(evidence_pages(nested))
    return sorted(pages)


def validate_fact_evidence(fact: dict[str, str], quote: str, context: str) -> None:
    for field in ("target", "condition", "unit", "cap", "frequency", "period", "exceptions"):
        if fact[field] and fact[field] not in quote:
            raise ValueError(f"{context} {field} is not linked to its evidence quote")
    value_numbers = numbers(fact["value"])
    if value_numbers and not value_numbers <= numbers(quote):
        raise ValueError(f"{context} has a value not linked to its evidence quote")
    if not value_numbers and fact["value"] not in quote:
        raise ValueError(f"{context} has a non-numeric value not linked to its evidence quote")
    if fact["value"] == "0" and "0" not in numbers(quote):
        raise ValueError(f"{context} zero is not explicit in its evidence quote")


def compare_ocr_outputs(luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> dict[str, Any]:
    """Page-aligned OCR comparison audit; relation validation remains the correctness gate."""
    lanes: dict[str, dict[int, str]] = {}
    for provider, payload in (("luna", luna_payload), ("upstage", upstage_payload)):
        pages: dict[int, str] = {}
        for row in payload.get("pages", []):
            page = row.get("page", row.get("number")) if isinstance(row, dict) else None
            text = row.get("text") if isinstance(row, dict) else None
            if isinstance(page, bool) or not isinstance(page, int) or not isinstance(text, str):
                raise ValueError(f"{provider} page format invalid")
            if page in pages:
                raise ValueError(f"{provider} page number is duplicated")
            pages[page] = normalized(text)
        lanes[provider] = pages
    page_numbers = sorted(set(lanes["luna"]) | set(lanes["upstage"]))
    rows = []
    for page in page_numbers:
        luna_text, upstage_text = lanes["luna"].get(page, ""), lanes["upstage"].get(page, "")
        luna_tokens, upstage_tokens = set(luna_text.split()), set(upstage_text.split())
        union = luna_tokens | upstage_tokens
        rows.append({
            "page": page,
            "luna_present": page in lanes["luna"],
            "upstage_present": page in lanes["upstage"],
            "normalized_text_equal": luna_text == upstage_text,
            "token_jaccard": 1.0 if not union else len(luna_tokens & upstage_tokens) / len(union),
            "luna_numbers": sorted(numbers(luna_text)),
            "upstage_numbers": sorted(numbers(upstage_text)),
        })
    return {
        "purpose": "diagnostic_only_not_a_correctness_or_selection_gate",
        "page_count_equal": set(lanes["luna"]) == set(lanes["upstage"]),
        "all_normalized_text_equal": all(row["normalized_text_equal"] for row in rows),
        "pages": rows,
    }


def lane_path(root: Path, document_id: str) -> Path:
    return root / f"{document_id.replace('/', '__')}.json"


class ProviderAdapter(Protocol):
    provider: str

    def load(self, document_id: str) -> tuple[Path, dict[str, Any]]: ...


class EmbeddingAdapter(Protocol):
    model: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> tuple[np.ndarray, dict[str, Any]]: ...


class OpenAIEmbeddingAdapter:
    """Explicitly constructed document-embedding boundary; construction performs no I/O."""

    model = "text-embedding-3-small"
    dimension = 1536

    def __init__(
        self,
        *,
        api_key: str | None,
        batch_size: int = 64,
        client: Any = None,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("embedding batch size must be positive")
        self.api_key = api_key
        self.batch_size = batch_size
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for approved document embedding")
        from openai import OpenAI

        self._client = OpenAI(api_key=self.api_key, max_retries=0)
        return self._client

    def embed_documents(self, texts: list[str]) -> tuple[np.ndarray, dict[str, Any]]:
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("document embedding requires non-empty texts")
        vectors: list[list[float]] = []
        provider_usage: list[dict[str, Any]] = []
        client = self._get_client()
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = client.embeddings.create(
                input=batch,
                model=self.model,
                dimensions=self.dimension,
                encoding_format="float",
                timeout=60.0,
            )
            data = sorted(response.data, key=lambda row: row.index)
            if [row.index for row in data] != list(range(len(batch))):
                raise ValueError("embedding response indices do not match the request batch")
            vectors.extend(row.embedding for row in data)
            usage = getattr(response, "usage", None)
            if hasattr(usage, "model_dump"):
                provider_usage.append(usage.model_dump())
            elif isinstance(usage, dict):
                provider_usage.append(dict(usage))
            else:
                provider_usage.append({})
        array = np.asarray(vectors, dtype=np.float32)
        if array.shape != (len(texts), self.dimension) or not np.isfinite(array).all():
            raise ValueError("embedding response shape or finiteness mismatch")
        return array, {
            "provider_called": True,
            "model": self.model,
            "dimension": self.dimension,
            "item_count": len(texts),
            "request_count": len(provider_usage),
            "batch_size": self.batch_size,
            "provider_usage": provider_usage,
        }


class LocalJsonAdapter:
    """Injection boundary for a provider lane; it never reads another lane."""

    def __init__(self, provider: str, root: Path) -> None:
        self.provider, self.root = provider, root

    def load(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        path = lane_path(self.root, document_id)
        if not path.is_file():
            raise FileNotFoundError(f"{self.provider} artifact missing: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("document_id") != document_id:
            raise ValueError(f"{self.provider} artifact document_id mismatch")
        if not isinstance(payload.get("pages"), list) or not isinstance(payload.get("facts"), list):
            raise ValueError(f"{self.provider} artifact requires pages and facts arrays")
        return path, payload


def load_lane(provider: str, root: Path, document_id: str) -> tuple[Path, dict[str, Any]]:
    return LocalJsonAdapter(provider, root).load(document_id)


def _line_registry(pages: dict[int, str]) -> dict[str, tuple[int, str]]:
    numbered = numbered_ocr_pages([{"page": page, "text": text} for page, text in sorted(pages.items())])
    return {
        # Preserve the OCR line exactly for canonical evidence/chunking.  All
        # comparisons explicitly call normalized() instead of discarding source text.
        row["line_id"]: (page["page"], str(row["text"]))
        for page in numbered
        for row in page["lines"]
    }


def _resolve_evidence(
    provider: str,
    context: str,
    evidence: Any,
    pages: dict[int, str],
    registry: dict[str, tuple[int, str]],
) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise ValueError(f"{provider} {context} evidence is required")
    if "line_ids" in evidence:
        line_ids = evidence["line_ids"]
        if not isinstance(line_ids, list) or not line_ids or not all(isinstance(line_id, str) and line_id in registry for line_id in line_ids):
            raise ValueError(f"{provider} {context} evidence line_ids are invalid")
        if len(set(line_ids)) != len(line_ids):
            raise ValueError(f"{provider} {context} evidence line_ids are duplicated")
        order = {line_id: ordinal for ordinal, line_id in enumerate(registry)}
        line_ids = sorted(line_ids, key=order.__getitem__)
        spans = [{"page": registry[line_id][0], "line_id": line_id, "quote": registry[line_id][1]} for line_id in line_ids]
        return {
            "provider": provider,
            "page": spans[0]["page"],
            "quote": normalized(" ".join(span["quote"] for span in spans)),
            "line_ids": line_ids,
            "spans": spans,
        }
    page = evidence.get("page")
    quote = normalized(evidence.get("quote", evidence.get("text", "")))
    if page not in pages or not quote or quote not in normalized(pages[page]):
        raise ValueError(f"{provider} {context} evidence is not grounded in its own OCR text")
    return {"provider": provider, "page": page, "quote": quote}


def _table_cells(line: str) -> list[tuple[int, int, str]]:
    """Return exact non-empty Markdown-pipe cell ranges, rejecting loose tables."""
    if not line.strip().startswith("|") or not line.rstrip().endswith("|"):
        raise ValueError("table row must use explicit Markdown pipe cells")
    cells: list[tuple[int, int, str]] = []
    delimiter_positions = [index for index, character in enumerate(line) if character == "|"]
    for left, right in zip(delimiter_positions, delimiter_positions[1:]):
        raw = line[left + 1:right]
        content = raw.strip()
        if not content:
            raise ValueError("table has an empty or merged cell")
        start = left + 1 + len(raw) - len(raw.lstrip())
        cells.append((start, start + len(content), content))
    if len(cells) < 2:
        raise ValueError("table requires at least two cells")
    return cells


def _table_header_role(value: str) -> str | None:
    text = normalized(value)
    if re.search(r"한도", text):
        return "cap"
    if re.search(r"(?:전월|지난달).?실적|이용.?금액|조건", text):
        return "condition"
    if re.search(r"횟수|이용.?회", text):
        return "frequency"
    if re.search(r"기간|개월|연간|월간", text):
        return "period"
    if re.search(r"제외|불가|유의", text):
        return "exceptions"
    if re.search(r"대상|업종|가맹점|서비스", text):
        return "target"
    if re.search(r"할인율|할인률|적립률|적립율|할인|적립", text):
        return "value"
    return None


def _fee_row_label(header: list[tuple[int, int, str]], row: list[tuple[int, int, str]], fact: dict[str, Any]) -> str | None:
    """Recognize an explicit fee label/value row, not an arbitrary unknown table."""
    if len(header) != 2 or len(row) != 2 or re.sub(r"\s+", "", header[0][2]) not in {"구분", "항목"}:
        return None
    if NUMBER_TOKEN.search(header[1][2]) or CRITICAL_RELATION_LINE.search(header[1][2]) or _table_header_role(header[1][2]) is not None:
        return None  # Never reinterpret a condition/limit column as a fee value.
    heading = re.sub(r"\s+", "", header[1][2])
    variants = set(heading.split("/"))
    all_variants = ("국내전용" in variants and bool(variants & {"국내외겸용", "해외겸용"})
                    and variants <= {"국내전용", "국내외겸용", "해외겸용", "K-world"})
    if heading not in {"금액", "비용", "내용"} and not all_variants:
        return None  # A lone family/domestic category is a restriction, not a value label.
    label = normalized(row[0][2])
    if not re.fullmatch(r"(?:기본|제휴|총|발급)?(?:연회비|수수료|이용료|발급비)", label):
        return None
    if normalized(fact["benefit_type"]) != label or label not in normalized(fact["target"]):
        return None
    return label




def _contains_range(container: tuple[str, int, int], line_id: str, start: int, end: int) -> bool:
    return container[0] == line_id and container[1] <= start and end <= container[2]


def _context_literal_ranges(text: str, field: str) -> list[tuple[int, int]]:
    """Recognize complete non-monetary tokens, never exempt their whole line."""
    if field == "cap":
        return [match.span() for match in EXPLICIT_CAP.finditer(text)]
    if field not in {"condition", "period", "exceptions"}:
        return []
    ranges = []
    if field == "period":
        # A complete duration/deadline atom establishes its temporal role even
        # when the OCR does not literally include the heading word '기간'.
        ranges.extend(match.span() for match in re.finditer(
            r"(?<![\d.])\d+\s*(?:영업일|일|개월|년)\s*(?:이내|이전|이후)(?:에)?", text))
    for match in CLOCK_LITERAL.finditer(text):
        # Do not reinterpret the tail of an invalid '오전23시' as a 24-hour clock.
        if re.search(r"(?:오전|오후)\s*$", text[:match.start()]):
            continue
        ranges.append(match.span())
    for match in DATE_LITERAL.finditer(text):
        year, month, day, iso_month, iso_day = match.groups()
        try:
            datetime(int(year), int(month or iso_month), int(day or iso_day))
        except ValueError:
            continue
        ranges.append(match.span())
    if field == "exceptions":
        for match in PHONE_LITERAL.finditer(text):
            if CONTACT_LABEL.search(text[:match.start()]):
                ranges.append(match.span())
    return ranges


def _check_heading_chain(provider: str, ordinal: int, ordered: list[str], registry: dict[str, tuple[int, str]]) -> None:
    """A common heading may be an ancestor, but sibling sections cannot be mixed."""
    selected = set(ordered)
    paths: list[tuple[str, ...]] = []
    stack: list[tuple[int, str]] = []
    current_page = None
    for line_id, (page, text) in registry.items():
        if page != current_page:
            stack.clear()
            current_page = page
        heading = HEADING_RE.fullmatch(text)
        if heading:
            depth = len(heading[1])
            while stack and stack[-1][0] >= depth:
                stack.pop()
            stack.append((depth, line_id))
        if line_id in selected:
            paths.append(tuple(item[1] for item in stack))
    deepest = max(paths, key=len, default=())
    if any(path != deepest[:len(path)] or (deepest and not path) for path in paths):
        raise ValueError(f"{provider} fact {ordinal} relation_scope crosses sibling heading sections")


def _fragment_format_key(value: str) -> str:
    """Normalize presentation only; numbers, units, operators and negation survive."""
    text = normalized(EVIDENCE_FORMAT_PREFIX.sub("", unicodedata.normalize("NFKC", value))).strip()
    return re.sub(r"(?<!\d)\s+|\s+(?!\d)", "", text)


def _shared_table_heading(scope: dict[str, Any], registry: dict[str, tuple[int, str]]) -> str | None:
    """Prove the narrow heading -> single table shape; never guess plain titles."""
    ids = list(registry)
    header = scope["header_line_ids"][0]
    index = ids.index(header)
    if index == 0:
        return None
    title_id = ids[index - 1]
    page, title = registry[title_id]
    heading = HEADING_RE.fullmatch(title)
    if not heading or page != registry[header][0] or title_id not in scope["line_ids"]:
        return None
    if set(scope["line_ids"]) != {title_id, header, scope["row_line_ids"][0]}:
        return None
    # A real delimiter row anchors the columns; a pipe in prose is not a table.
    if index + 1 >= len(ids) or not re.fullmatch(r"\s*\|(?:\s*:?-+:?\s*\|){2,}\s*", registry[ids[index + 1]][1]):
        return None
    ended = False
    for line_id in ids[index:]:
        line_page, text = registry[line_id]
        if line_page != page:
            break
        following_heading = HEADING_RE.fullmatch(text)
        if following_heading:
            if len(following_heading[1]) <= len(heading[1]):
                break
            return None  # Nested sections require their own proof, not proximity.
        if text.strip().startswith("|"):
            if ended:
                return None  # A second table makes the title's scope ambiguous.
        else:
            ended = True
            if not _is_generic_layout_line(text):
                return None  # Do not silently drop surrounding rules/exceptions.
    return title_id


def _resolve_field_fragment(ref: Any, registry: dict[str, tuple[int, str]]) -> tuple[str, int, int]:
    """Use a coordinate as a hint, with the same rule for approval and diagnosis."""
    if not isinstance(ref, dict) or not isinstance(ref.get("line_id"), str) or ref["line_id"] not in registry or not isinstance(ref.get("fragment"), str) or not ref["fragment"]:
        raise ValueError("source occurrence is unresolved")
    line_id = ref["line_id"]
    start, end = ref.get("char_start"), ref.get("char_end")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        start, end = 0, 0
    start, end = _same_line_fragment_range(registry[line_id][1], ref["fragment"], start, end)
    return line_id, start, end


def _same_line_fragment_range(
    source_line: str,
    fragment: str,
    start: int,
    end: int,
    allowed_ranges: list[tuple[int, int]] | None = None,
) -> tuple[int, int]:
    """Repair a provider coordinate only from a unique match on its stated line."""
    allowed_ranges = allowed_ranges or [(0, len(source_line))]
    within = lambda left, right: any(low <= left and right <= high for low, high in allowed_ranges)
    wanted = _fragment_format_key(fragment)
    if end <= len(source_line) and within(start, end) and _fragment_format_key(source_line[start:end]) == wanted:
        return start, end

    exact = [
        (match.start(), match.end())
        for match in re.finditer(re.escape(fragment), source_line)
        if within(match.start(), match.end())
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError("fragment has ambiguous repeated candidates on its source line")

    projected: list[str] = []
    positions: list[tuple[int, int]] = []
    for index, character in enumerate(source_line):
        for normalized_character in unicodedata.normalize("NFKC", character):
            if normalized_character.isspace():
                if projected and projected[-1] == " ":
                    positions[-1] = (positions[-1][0], index + 1)
                    continue
                normalized_character = " "
            projected.append(normalized_character)
            positions.append((index, index + 1))
    compacted: list[str] = []
    compacted_positions: list[tuple[int, int]] = []
    for index, character in enumerate(projected):
        if character == " ":
            previous = projected[index - 1] if index else ""
            following = projected[index + 1] if index + 1 < len(projected) else ""
            if not previous.isdigit() or not following.isdigit():
                continue
        compacted.append(character)
        compacted_positions.append(positions[index])
    haystack, positions = "".join(compacted), compacted_positions
    matches = [
        (positions[match.start()][0], positions[match.end() - 1][1])
        for match in re.finditer(re.escape(wanted), haystack)
        if wanted and within(positions[match.start()][0], positions[match.end() - 1][1])
    ]
    if len(set(matches)) != 1:
        raise ValueError("fragment has no unique formatting-equivalent candidate on its source line")
    return matches[0]


def _named_table_note(scope: dict[str, Any], line_id: str,
                      registry: dict[str, tuple[int, str]]) -> tuple[str, tuple[int, int]] | None:
    """Prove one explicitly named note belongs to one source table row.

    Provider coordinates and JSON field values never determine the target.
    Other notes may intervene; another table/section or duplicate target may not.
    """
    if scope.get('scope_type') != 'table_row':
        return None
    header_ids, row_ids = scope.get('header_line_ids', []), scope.get('row_line_ids', [])
    if len(header_ids) != 1 or len(row_ids) != 1 or any(k not in registry for k in [*header_ids, *row_ids, line_id]):
        return None
    header, row = header_ids[0], row_ids[0]
    note = NAMED_TABLE_NOTE.fullmatch(registry[line_id][1])
    if not note or len({registry[k][0] for k in (header, row, line_id)}) != 1:
        return None
    try:
        headers, cells = _table_cells(registry[header][1]), _table_cells(registry[row][1])
        roles = [_table_header_role(cell[2]) for cell in headers]
        if roles.count('target') != 1 or roles.count('value') != 1 or len(cells) != len(headers):
            return None
        target_index = roles.index('target')
        target = _fragment_format_key(cells[target_index][2])
        if target != _fragment_format_key(note[1]):
            return None
        actions = set(BENEFIT_VERB_SIGNAL.findall(headers[roles.index('value')][2]))
        if actions != {note[2].removeprefix('청구')}:
            return None
        ids = list(registry)
        hi, ri, ni = ids.index(header), ids.index(row), ids.index(line_id)
        end = ri
        while end + 1 < len(ids) and registry[ids[end + 1]][0] == registry[row][0] and registry[ids[end + 1]][1].strip().startswith('|'):
            end += 1
        if ni <= end or any(registry[k][1].strip().startswith('|') or HEADING_RE.fullmatch(registry[k][1]) for k in ids[end + 1:ni]):
            return None
        matches = 0
        for k in ids[hi + 1:end + 1]:
            if re.fullmatch(r'[\s|:\-]+', registry[k][1]):
                continue
            other = _table_cells(registry[k][1])
            if len(other) != len(headers):
                return None
            matches += _fragment_format_key(other[target_index][2]) == target
        if matches != 1:
            return None
    except ValueError:
        return None
    role = 'exceptions' if '제외' in note[3] else 'cap' if note[3] == '한도' else 'condition'
    return role, note.span(4)




def _list_marker_ranges(registry: dict[str, tuple[int, str]]) -> dict[str, tuple[int, int]]:
    """Recognize consecutive list labels, never amounts, decimals or inline refs."""
    pattern = re.compile(r"^\s*(?:[-*•·]\s+)*(?P<label>[①-⑳]|[1-9]\d?[.)]|\([1-9]\d?\))\s+(?=\S)")
    candidates = []
    lines = list(registry)
    for index, line_id in enumerate(lines):
        page, text = registry[line_id]
        match = pattern.match(text)
        if match:
            label = match['label']
            number = int(unicodedata.normalize('NFKC', label).strip('().'))
            style = 'circled' if label[0] in '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳' else re.sub(r'\d+', '#', label)
            candidates.append((index, line_id, page, number, style, match.span('label')))
    result = {}
    for left, right in zip(candidates, candidates[1:]):
        if left[2] == right[2] and left[4] == right[4] and right[3] == left[3] + 1 and right[0] - left[0] <= 6 and not any(HEADING_RE.fullmatch(registry[k][1]) for k in lines[left[0] + 1:right[0]]):
            result[left[1]], result[right[1]] = left[5], right[5]
    return result


def _strip_confirmed_list_labels(value: str, registry: dict[str, tuple[int, str]], line_ids: Iterable[str]) -> str:
    """Remove a label only alongside its complete, explicitly referenced item text."""
    markers = _list_marker_ranges(registry)
    for line_id in line_ids:
        if line_id not in markers:
            continue
        start, end = markers[line_id]
        line = registry[line_id][1]
        label, body = line[start:end], line[end:].strip()
        body_pattern = r'\s+'.join(re.escape(word) for word in body.split())
        value = re.sub(r'(?<!\S)' + re.escape(label) + r'\s+(?=' + body_pattern + r'(?:\s|$))', '', value)
    return value


def _comparison_fact(raw: dict[str, Any], registry: dict[str, tuple[int, str]]) -> dict[str, Any]:
    comparable = dict(raw)
    fields = raw.get('field_evidence', {})
    if isinstance(fields, dict):
        for field in ('condition', 'cap', 'frequency', 'period', 'exceptions'):
            fragments = fields.get(field)
            if isinstance(raw.get(field), str) and isinstance(fragments, list):
                ids = [x['line_id'] for x in fragments if isinstance(x, dict) and isinstance(x.get('line_id'), str)]
                comparable[field] = _strip_confirmed_list_labels(raw[field], registry, ids)
    return normalise_fact(comparable)


def _validate_field_grounding(
    provider: str, ordinal: int, raw_fact: dict[str, Any],
    fact: dict[str, Any], registry: dict[str, tuple[int, str]],
    card_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify role-bound claims in a source witness, not a reconstructed paragraph.

    Provider scope/coordinates are hints. The witness below is one table row or
    one bounded benefit block. Unresolved witnesses never authorize a fact.
    """
    declared = raw_fact.get("relation_scope")
    supplied_fields = raw_fact.get("field_evidence")
    if not isinstance(declared, dict) or not isinstance(supplied_fields, dict):
        raise ValueError(f"{provider} fact {ordinal} source witness hints are missing")
    if set(supplied_fields) != set(RELATION_FIELDS):
        raise ValueError(f"{provider} fact {ordinal} field_evidence keys are incomplete")
    ids = list(registry)
    order = {line_id: i for i, line_id in enumerate(ids)}
    field_spans: dict[str, list[tuple[str, int, int]]] = {}
    source_fields = {field: "" for field in RELATION_FIELDS}
    evidence: dict[str, Any] = {}
    for field in RELATION_FIELDS:
        refs = supplied_fields[field]
        if not isinstance(refs, list):
            raise ValueError(f"{provider} fact {ordinal} {field} evidence must be an array")
        if not fact[field] and not refs:
            field_spans[field] = []
            continue
        if not refs:
            raise ValueError(f"{provider} fact {ordinal} {field} source occurrence is unresolved")
        spans = []
        for ref in refs:
            spans.append(_resolve_field_fragment(ref, registry))
        spans = sorted(set(spans), key=lambda x: (order[x[0]], x[1], x[2]))
        if any(a[0] == b[0] and a[2] > b[1] for a, b in zip(spans, spans[1:])):
            raise ValueError(f"{provider} fact {ordinal} overlapping evidence prevents role comparison")
        field_spans[field] = spans
        source = " ".join(registry[k][1][a:b] for k, a, b in spans)
        if field in {"condition", "cap", "frequency", "period", "exceptions"}:
            source = _strip_confirmed_list_labels(source, registry, [k for k, _, _ in spans])
        source_fields[field] = source
        resolved = _resolve_evidence(provider, f"fact {ordinal} {field}",
                                     {"line_ids": list(dict.fromkeys(k for k, _, _ in spans))}, {}, registry)
        evidence[field] = {**resolved, "fragments": [
            {"line_id": k, "fragment": registry[k][1][a:b], "char_start": a, "char_end": b}
            for k, a, b in spans]}
    source_fact = normalise_fact(source_fields)
    for field in RELATION_FIELDS:
        if field_comparison_key(source_fact, field) != field_comparison_key(fact, field):
            material = (field in {"value", "unit", "action"}
                        or typed_literals(source_fact[field]) != typed_literals(fact[field])
                        or condition_operators(source_fact[field]) != condition_operators(fact[field]))
            error = CriticalContentMismatch if material else ValueError
            raise error(f"{provider} fact {ordinal} {field} material claim differs or its meaning is unresolved")
    # A positive verb inside an exclusion noun is not a positive benefit.
    # Preserve the polarity on that action, even if another field quotes the
    # negative text (e.g. condition='적립 제외 대상', action='적립').
    if BENEFIT_VERB_SIGNAL.fullmatch(normalized(fact["action"])):
        for k, _, end in field_spans["action"]:
            tail = registry[k][1][end:]
            if re.match(r"\s*(?:(?:대상(?:에서)?|이|가)\s*)?(?:제외|불가|미적용|되지\s*않|하지\s*않)", tail):
                raise CriticalContentMismatch(f"{provider} fact {ordinal} positive action cites a negated benefit")
    selected = {k for spans in field_spans.values() for k, _, _ in spans}
    if not selected:
        raise ValueError(f"{provider} fact {ordinal} source witness is empty")
    scope_ids = sorted(selected, key=order.__getitem__)
    scope = {"scope_type": "bounded_block", "line_ids": scope_ids,
             "header_line_ids": [], "row_line_ids": []}
    table_roles = []
    fee_label = None
    shared_heading = None
    header_ids, row_ids = declared.get("header_line_ids", []), declared.get("row_line_ids", [])
    table_rows = {k for field in ("target", "value", "unit") for k, _, _ in field_spans[field]
                  if registry[k][1].strip().startswith("|")}
    if declared.get("scope_type") == "table_row" or table_rows or any(registry[k][1].lstrip().startswith("|") for k in selected):
        if not isinstance(header_ids, list) or not isinstance(row_ids, list) or len(header_ids) != 1 or len(row_ids) != 1:
            raise ValueError(f"{provider} fact {ordinal} table scope requires one header and one row")
        header, row = header_ids[0], row_ids[0]
        if header not in registry or row not in registry or registry[header][0] != registry[row][0] or order[header] >= order[row]:
            raise ValueError(f"{provider} fact {ordinal} table header-to-row relation is unresolved")
        between = ids[order[header]:order[row]+1]
        if any(not registry[k][1].strip().startswith("|") for k in between):
            raise ValueError(f"{provider} fact {ordinal} table header belongs to another table")
        cells, row_cells = _table_cells(registry[header][1]), _table_cells(registry[row][1])
        if len(cells) != len(row_cells):
            raise ValueError(f"{provider} fact {ordinal} table cell roles are unresolved")
        table_roles = [_table_header_role(text) for _, _, text in cells]
        fee_label = _fee_row_label(cells, row_cells, fact)
        if fee_label:
            table_roles = ["target", "value"]
        scope.update(scope_type="table_row", header_line_ids=[header], row_line_ids=[row],
                     line_ids=sorted(selected | {header, row}, key=order.__getitem__))
        scope_ids = scope["line_ids"]
        if table_rows - {row, header}:
            raise CriticalContentMismatch(f"{provider} fact {ordinal} benefit fields borrow another table row")
        for field, role in TABLE_FIELD_ROLE.items():
            for k, a, b in field_spans[field]:
                if k == row:
                    columns = [j for j, (lo, hi, _) in enumerate(row_cells) if lo <= a and b <= hi]
                    if len(columns) != 1:
                        raise ValueError(f"{provider} fact {ordinal} {field} cell occurrence is unresolved")
                    actual = table_roles[columns[0]]
                    if actual and actual != role:
                        raise CriticalContentMismatch(f"{provider} fact {ordinal} {field} is assigned to the wrong table role")
        # External context requires an explicit named note or a proven shared
        # heading; its distance by itself is not evidence for or against it.
        shared = shared_heading = _shared_table_heading(scope, registry)
        for k in selected - {header, row}:
            named = _named_table_note(scope, k, registry)
            if named:
                role, (a, b) = named
                if not any(_contains_range(span, k, a, b) for span in field_spans[role]):
                    raise CriticalContentMismatch(f"{provider} fact {ordinal} named note is assigned to the wrong role")
                continue
            if k == shared:
                continue
            # A complete, contiguous common rule block can be a witness.
            block_start = order[header]
            while block_start and registry[ids[block_start-1]][1].strip().startswith("|"):
                block_start -= 1
            block_end = order[row]
            while block_end+1 < len(ids) and registry[ids[block_end+1]][1].strip().startswith("|"):
                block_end += 1
            nearest = range(order[k]+1, block_start) if order[k] < block_start else range(block_end+1, order[k])
            if block_start <= order[k] <= block_end or registry[k][0] != registry[row][0] or any(
                ids[i] not in selected and not _is_generic_layout_line(registry[ids[i]][1]) for i in nearest
            ):
                raise ValueError(f"{provider} fact {ordinal} external rule applicability is unresolved")
            if fee_label and order[k] == order[row] + 1 and field_spans["target"]:
                expected = re.sub(r"\s+", "", card_name or "")
                actual = re.sub(r"\s+", "", fact["target"])
                if actual not in {fee_label, expected + "의별도" + fee_label, expected + "별도" + fee_label}:
                    raise ValueError(f"{provider} fact {ordinal} fee target belongs to another entity")
                continue
            if any(span[0] == k for field in ("target", "value", "unit") for span in field_spans[field]):
                raise ValueError(f"{provider} fact {ordinal} shared benefit heading is unresolved")
        # A row role must retain all of its material content, not all characters.
        for j, role in enumerate(table_roles):
            if not role:
                continue
            row_value = row_cells[j][2]
            if fee_label and role == "target":
                continue  # Entity-qualified target was checked against card identity.
            owned = " ".join(registry[k][1][a:b] for field in
                             (("value", "unit", "action") if role == "value" else (role,))
                             for k, a, b in field_spans[field] if k == row and row_cells[j][0] <= a < b <= row_cells[j][1])
            if material_text_key(row_value) != material_text_key(owned):
                raise ValueError(f"{provider} fact {ordinal} table {role} material content is missing or unresolved")
        inherited = any(fact[field] and TABLE_FIELD_ROLE[field] not in table_roles
                        and not (fee_label and field == "target") for field in ("target", "value", "unit"))
        if inherited:
            if not shared_heading:
                raise ValueError(f"{provider} fact {ordinal} inherited benefit relation is unresolved")
            title = registry[shared_heading][1]
            heading = HEADING_RE.fullmatch(title)
            # Do not hide a second target/rate/exception inside benefit_type.
            title_spans = [span for field, spans in field_spans.items() if field != "benefit_type" for span in spans]
            if any(not character.isspace() and not any(_contains_range(span, shared_heading, index, index + 1) for span in title_spans)
                   for index, character in enumerate(title) if heading.start(2) <= index < heading.end(2)):
                raise ValueError(f"{provider} fact {ordinal} shared heading contains unmapped relationship content")
            if not field_spans.get("action") or not all(span[0] == shared_heading for span in field_spans["action"]):
                raise ValueError(f"{provider} fact {ordinal} shared heading action is not directly grounded")
            if "value" not in table_roles and fact["value"] and len(fact["typed_normalization"].get("value", [])) != 1:
                raise ValueError(f"{provider} fact {ordinal} shared heading value is not a single numeric benefit")
            if "value" not in table_roles and fact["value"]:
                actions = field_spans["action"]
                amounts = field_spans["value"] + field_spans.get("unit", [])
                amount_start, amount_end = min(span[1] for span in amounts), max(span[2] for span in amounts)
                number = NUMBER_TOKEN.match(title, amount_start)
                source_unit = SOURCE_UNIT.match(title, number.end()) if number else None
                if not source_unit or source_unit.end() != amount_end:
                    raise ValueError(f"{provider} fact {ordinal} shared heading amount must be one number with its unit")
                directly_bound = False
                if len(actions) == 1 and BENEFIT_VERB_SIGNAL.fullmatch(fact["action"]):
                    _, action_start, action_end = actions[0]
                    if amount_end <= action_start:
                        directly_bound = not title[amount_end:action_start].strip()
                    elif action_end <= amount_start:
                        directly_bound = not title[action_end:amount_start].strip()
                if not directly_bound:
                    raise ValueError(f"{provider} fact {ordinal} shared heading value is not directly bound to its benefit action")
            for field, marker in FIELD_ROLE_MARKERS.items():
                for match in marker.finditer(title):
                    if not any(_contains_range(span, shared_heading, match.start(), match.end()) for span in field_spans.get(field, [])):
                        raise ValueError(f"{provider} fact {ordinal} shared heading {field} role is not preserved")
    else:
        if len({registry[k][0] for k in selected}) != 1:
            raise ValueError(f"{provider} fact {ordinal} cross-page benefit relation is unresolved")
        if len(scope_ids) == 1:
            scope["scope_type"] = "sentence"
        else:
            skipped = ids[order[scope_ids[0]]:order[scope_ids[-1]]+1]
            if any(k not in selected for k in skipped):
                _check_heading_chain(provider, ordinal, scope_ids, registry)
            if any(k not in selected and CRITICAL_RELATION_LINE.search(registry[k][1])
                   and not _is_generic_layout_line(registry[k][1]) for k in skipped):
                raise ValueError(f"{provider} fact {ordinal} intervening benefit prevents a unique relationship")
    # Only verified witness lines participate; unrelated provider scope entries
    # are not silently attributed to this benefit. Coverage is checked per lane.
    list_markers = _list_marker_ranges(registry)
    gap_lines = {k: text[:list_markers[k][0]] + " " * (list_markers[k][1] - list_markers[k][0]) + text[list_markers[k][1]:]
                 if k in list_markers else text for k, (_, text) in registry.items()}
    all_spans = [span for spans in field_spans.values() for span in spans]
    # An explicit logical expression must retain every operand, not just its
    # connector. This is NOT a sentence reconstruction gate for other fields:
    # their values, roles, numeric ownership and negative operators are checked
    # separately below. Unparsed prose cannot be declared semantically verified.
    for field in ("target", "condition", "cap", "frequency", "period", "exceptions"):
        for k in {span[0] for span in field_spans[field]}:
            if registry[k][1].lstrip().startswith("|"):
                continue  # The independently matched table cell is checked above.
            own = [span for span in field_spans[field] if span[0] == k]
            start, end = min(a for _, a, _ in own), max(b for _, _, b in own)
            other = [span for name, spans in field_spans.items() if name != field for span in spans if span[0] == k]
            left = max((b for _, _, b in other if b <= start), default=0)
            right = min((a for _, a, _ in other if end <= a), default=len(registry[k][1]))
            named = _named_table_note(scope, k, registry) if scope["scope_type"] == "table_row" else None
            if named and named[0] == field:
                left, right = named[1]
            source = gap_lines[k][left:right]
            connector = re.search(r"또는|혹은|및|그리고|(?i:\band\b|\bor\b)", source)
            if not connector:
                continue
            # Check the operand tail, not unrelated text before this expression.
            connector_start, operand_start = left + connector.start(), left + connector.end()
            if not any(a < connector_start and registry[k][1][a:min(b, connector_start)].strip()
                       for _, a, b in own):
                raise ValueError(f"{provider} fact {ordinal} {field} logical operand is missing")
            left = operand_start
            # A verified role may overlap the tail (e.g. target '기본 서비스'
            # and value '기본 서비스만'). Its qualifier is verified in that
            # role, not falsely required a second time in this target.
            right = min((a for _, a, b in other if operand_start < a < right and b >= end), default=right)
            source = gap_lines[k][left:right]
            if left == 0:
                source = re.sub(r"^\s*(?:#{1,6}\s+|(?:[-*•·]\s+)+)", "", source)
            source = source.strip(" ()[]:：,，;.。")
            source = re.sub(r"^(?:은|는|이|가)\s+", "", source)
            preserved = " ".join(registry[k][1][max(a, left):min(b, right)]
                                 for _, a, b in own if b > left and a < right)
            preserved = _strip_confirmed_list_labels(preserved, registry, [k])
            if not material_text_key(preserved) or material_text_key(source) != material_text_key(preserved):
                raise ValueError(f"{provider} fact {ordinal} {field} predicate has unaccounted material content")
    if fee_label:
        # A fee row cannot silently discard its immediately following rules.
        ids = list(registry)
        cursor = ids.index(scope["row_line_ids"][0]) + 1
        while cursor < len(ids) and registry[ids[cursor]][1].strip().startswith("|"):
            cursor += 1
        while cursor < len(ids):
            line_id = ids[cursor]
            page, text = registry[line_id]
            if page != registry[scope["row_line_ids"][0]][0] or HEADING_RE.fullmatch(text) or text.strip().startswith("|"):
                break
            if line_id not in scope_ids:
                raise ValueError(f"{provider} fact {ordinal} adjacent fee rules are missing from relation_scope")
            remaining = "".join(character for index, character in enumerate(text)
                                if not any(_contains_range(span, line_id, index, index + 1) for span in all_spans))
            if not re.fullmatch(r"(?:[\s,.;:•※·-]|는|은|이|가|을|를|의)*", remaining):
                raise ValueError(f"{provider} fact {ordinal} adjacent fee condition or exception is not preserved")
            cursor += 1
    # Operators and negative/shared scope are content, even when they occur
    # outside quoted fragments. Unlike intervening prose, these may not vanish.
    material_markers = re.compile(r"제외|불가|미적용|않(?:음|습니다)|한하지|한함|한해|한하여|전용|만(?=\s|$|[,.）)])|이상|초과|이하|미만|통합|합산|공유|개별|또는|혹은|및|그리고|(?i:\band\b|\bor\b)|다만|(?<![가-힣])단(?=,)|[<>]=?")
    for k in scope_ids:
        for match in material_markers.finditer(registry[k][1]):
            if fee_label and k in scope["header_line_ids"]:
                continue  # _fee_row_label proved this column explicitly covers both card variants.
            if not any(_contains_range(span, k, match.start(), match.end())
                       for field, spans in field_spans.items() if field != "benefit_type" for span in spans):
                raise CriticalContentMismatch(f"{provider} fact {ordinal} material operator or exception is omitted")
    for line_id in scope_ids:
        source_line = registry[line_id][1]
        if line_id in set(scope.get("header_line_ids", [])):
            continue
        layout_heading = _is_generic_layout_line(source_line) or bool(re.match(r"^\s*#{1,6}\s+", source_line))
        for match in BENEFIT_VERB_SIGNAL.finditer(source_line):
            action_mapped = any(
                _contains_range(span, line_id, match.start(), match.end())
                for span in field_spans.get("action", [])
            )
            contextual_role = any(
                re.fullmatch(r"\s*", source_line[match.end():marker.start()])
                and any(_contains_range(span, line_id, marker.start(), marker.end()) for span in field_spans.get(field, []))
                for field in ("condition", "cap", "frequency", "period", "exceptions")
                for marker in FIELD_ROLE_MARKERS[field].finditer(source_line)
                if marker.start() >= match.end()
            )
            named = _named_table_note(scope, line_id, registry) if scope['scope_type'] == 'table_row' else None
            if named:
                role, (start, end) = named
                contextual_role = contextual_role or any(_contains_range(span, line_id, start, end) for span in field_spans.get(role, []))
            table_common_title = (
                scope["scope_type"] == "table_row"
                and line_id not in {scope["header_line_ids"][0], scope["row_line_ids"][0]}
                and not NUMBER_TOKEN.search(gap_lines[line_id])
                and any(_contains_range(span, line_id, match.start(), match.end()) for span in field_spans.get("benefit_type", []))
                and any(
                    span[0] == scope["header_line_ids"][0]
                    and normalized(registry[span[0]][1][span[1]:span[2]]) == normalized(match.group())
                    for span in field_spans.get("action", [])
                )
                and any(
                    _contains_range(span, line_id, marker.start(), marker.end())
                    for span in field_spans.get("condition", [])
                    for marker in FIELD_ROLE_MARKERS["condition"].finditer(source_line)
                )
            )
            # '적립률' or a '할인/적립' label names a concept, not another action.
            # It must still be preserved in an actual field; all numeric and
            # role checks below apply unchanged to the surrounding content.
            noun_end = BENEFIT_NOUN_SUFFIX.match(source_line, match.end())
            noun_ranges = [(match.start(), noun_end.end())] if noun_end else []
            noun_ranges.extend(
                label.span() for label in BENEFIT_COMBINED_LABEL.finditer(source_line)
                if label.start() <= match.start() and match.end() <= label.end()
            )
            explanatory_action = any(
                span[0] == line_id
                and not BENEFIT_VERB_SIGNAL.fullmatch(normalized(source_line[span[1]:span[2]]))
                for span in field_spans.get("action", [])
            )
            noun_mapped = explanatory_action and any(
                _contains_range(span, line_id, start, end)
                for start, end in noun_ranges
                for field, spans in field_spans.items() if field not in {"benefit_type", "action"}
                for span in spans
            )
            # '청구할인 제외' states an exclusion of the named benefit, not a
            # second positive discount. Both target and negative action must
            # remain explicitly grounded on this same source line.
            excluded_target = any(
                _contains_range(target_span, line_id, match.start(), match.end())
                and target_span[2] <= marker.start()
                and not source_line[target_span[2]:marker.start()].strip()
                and any(_contains_range(span, line_id, marker.start(), marker.end()) for span in field_spans.get("action", []))
                for target_span in field_spans.get("target", [])
                for marker in FIELD_ROLE_MARKERS["exceptions"].finditer(source_line)
            )
            if not layout_heading and not action_mapped and not contextual_role and not table_common_title and not noun_mapped and not excluded_target:
                raise ValueError(f"{provider} fact {ordinal} contains multiple benefit candidates")
    if scope["scope_type"] != "table_row":
        for line_id in scope_ids:
            source_line = registry[line_id][1]
            for field, marker in FIELD_ROLE_MARKERS.items():
                for match in marker.finditer(source_line):
                    permitted = ("exceptions", "condition", "action") if field == "exceptions" else (field,)
                    if (field == "condition" and re.sub(r"\s+", "", match.group()) == "이용금액"
                            and not NUMBER_TOKEN.search(gap_lines[line_id])
                            and FIELD_ROLE_MARKERS["exceptions"].search(fact["action"])):
                        # '무이자할부 이용금액 제외' names excluded spending; it
                        # does not introduce a numeric spending requirement.
                        permitted = ("condition", "exceptions", "target")
                    if not any(_contains_range(span, line_id, match.start(), match.end()) for owner in permitted for span in field_spans.get(owner, [])):
                        raise ValueError(f"{provider} fact {ordinal} marked {field} source range is not mapped to that field")
    numeric_owner: dict[tuple[str, int, int], str] = {}
    header_ids = set(scope.get("header_line_ids", []))
    for line_id in scope_ids:
        if line_id in header_ids:
            continue
        for cap in EXPLICIT_CAP.finditer(registry[line_id][1]):
            if not any(_contains_range(span, line_id, cap.start(), cap.end()) for span in field_spans.get('cap', [])):
                raise ValueError(f"{provider} fact {ordinal} explicit cap amount is not mapped to cap")
        for number_start, number_end in literal_number_spans(registry[line_id][1]):
            if line_id in list_markers and list_markers[line_id][0] <= number_start and number_end <= list_markers[line_id][1]:
                continue
            owners = [
                field for field, spans in field_spans.items()
                if field != "unit" and any(_contains_range(span, line_id, number_start, number_end) for span in spans)
            ]
            # Redundant label spans are not a second numeric owner. The actual
            # role must cover this very occurrence, not just an equal number.
            if "benefit_type" in owners and any(field != "benefit_type" for field in owners):
                owners.remove("benefit_type")
            if len(owners) != 1:
                raise ValueError(f"{provider} fact {ordinal} source numeric range is missing or ambiguously mapped")
            numeric_owner[(line_id, number_start, number_end)] = owners[0]
            if owners[0] in {"condition", "cap", "frequency", "period", "exceptions"}:
                table_role_bound = (
                    scope["scope_type"] == "table_row"
                    and line_id == scope["row_line_ids"][0]
                    and TABLE_FIELD_ROLE[owners[0]] in table_roles
                )
                marker_bound = any(
                    _contains_range(span, line_id, marker.start(), marker.end())
                    for span in field_spans[owners[0]]
                    for marker in FIELD_ROLE_MARKERS[owners[0]].finditer(registry[line_id][1])
                )
                context_literal_bound = any(
                    span[0] == line_id
                    and span[1] + start <= number_start and number_end <= span[1] + end
                    for span in field_spans[owners[0]]
                    for start, end in _context_literal_ranges(registry[span[0]][1][span[1]:span[2]], owners[0])
                )
                if not table_role_bound and not marker_bound and not context_literal_bound:
                    raise ValueError(f"{provider} fact {ordinal} numeric relationship is assigned to the wrong field role")
            suffix = SOURCE_UNIT.match(registry[line_id][1], number_end)
            if suffix:
                if owners[0] in {"benefit_type", "action", "target"}:
                    raise ValueError(f"{provider} fact {ordinal} benefit numeric range is assigned to a non-numeric field")
                unit_start = number_end
                while registry[line_id][1][unit_start].isspace():
                    unit_start += 1
                allowed = [*field_spans[owners[0]], *(field_spans.get("unit", []) if owners[0] == "value" else [])]
                if not all(any(_contains_range(span, line_id, pos, pos + 1) for span in allowed)
                           for pos in range(unit_start, suffix.end())):
                    raise ValueError(f"{provider} fact {ordinal} source numeric unit is missing from its owning field")
    if source_fact["unit"]:
        # Bare adjacent numbers are not a range or a compound Korean amount.
        if re.fullmatch(r"[\d,.]+(?:\s+[\d,.]+)+", source_fact["value"]):
            raise ValueError(f"{provider} fact {ordinal} value contains unconnected measurements")
        attached = False
        for (line_id, start, end), owner in numeric_owner.items():
            if owner != "value":
                continue
            source_line = registry[line_id][1]
            suffix = SOURCE_UNIT.match(source_line, end)
            unit_start = end
            while unit_start < len(source_line) and source_line[unit_start].isspace():
                unit_start += 1
            if suffix and any(
                span[0] == line_id and unit_start <= span[1] < span[2] == suffix.end()
                and re.fullmatch(r"[조억만천백십\s]*", source_line[unit_start:span[1]])
                and normalized(source_line[span[1]:span[2]]) == source_fact["unit"]
                for span in field_spans.get("unit", [])
            ):
                attached = True
                break
            # Extend only for named measures paired with a plain number.
            # A value containing prose must not bridge unrelated source facts.
            if not (NAMED_MEASURE_UNIT.fullmatch(source_fact["unit"])
                    and re.fullmatch(r"[+-]?\d+(?:,\d{3})*(?:\.\d+)?", source_fact["value"])):
                continue
            amount_spans = field_spans.get('value', []) + field_spans.get('unit', [])
            if any(span[0] == line_id and end <= span[1]
                   and normalized(source_line[span[1]:span[2]]) == source_fact['unit']
                   and all(source_line[pos].isspace() or any(
                       _contains_range(owner_span, line_id, pos, pos + 1)
                       for owner_span in amount_spans)
                       for pos in range(start, span[2]))
                   for span in field_spans.get('unit', [])):
                attached = True
                break
        if not attached:
            raise ValueError(f"{provider} fact {ordinal} unit is not attached to its value source number")
    scope_evidence = _resolve_evidence(provider, f"fact {ordinal} relation_scope", {"line_ids": scope_ids}, {}, registry)
    return (
        {"scope_type": scope["scope_type"], "line_ids": scope_ids, "evidence": scope_evidence},
        evidence,
    )


def validate_lane(provider: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("structure_schema_version") != STRUCTURE_SCHEMA_VERSION:
        raise LaneRestructureRequired(
            f"{provider} legacy structured payload is diagnostic-only; {STRUCTURE_SCHEMA_VERSION} is required"
        )
    if payload.get("provider") != provider or not isinstance(payload.get("source_pdf_sha256"), str):
        raise ValueError(f"{provider} provenance is invalid")
    if not isinstance(payload.get("pages"), list) or not isinstance(payload.get("facts"), list) or not payload["facts"]:
        raise ValueError(f"{provider} pages and non-empty facts arrays are required")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or not all(isinstance(provenance.get(key), str) and provenance[key] for key in ("endpoint", "model", "config_hash")):
        raise ValueError(f"{provider} endpoint/model/config provenance is required")
    pages: dict[int, str] = {}
    for page in payload["pages"]:
        page_number = page.get("page", page.get("number"))
        text = page.get("text")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1 or not isinstance(text, str):
            raise ValueError(f"{provider} page format invalid")
        if page_number in pages:
            raise ValueError(f"{provider} page number is duplicated")
        pages[page_number] = text
    if not pages:
        raise ValueError(f"{provider} has no pages")
    registry = _line_registry(pages)
    identity = payload.get("identity")
    if not isinstance(identity, dict) or not all(isinstance(identity.get(key), str) and normalized(identity[key]) for key in ("issuer_name", "card_name")):
        raise ValueError(f"{provider} identity is required")
    identity_evidence: dict[str, dict[str, Any]] = {}
    for key, label in (("issuer", "issuer_name"), ("card", "card_name")):
        try:
            resolved = _resolve_evidence(provider, f"{key} identity", identity.get(f"{key}_evidence"), pages, registry)
        except ValueError as error:
            raise ValueError(f"{provider} {key} identity is not grounded") from error
        if normalized(identity[label]) not in resolved["quote"]:
            raise ValueError(f"{provider} {key} identity is not grounded")
        identity_evidence[key] = resolved
    validated: list[dict[str, Any]] = []
    scope_fingerprints: set[tuple[Any, ...]] = set()
    fact_issues: list[dict[str, Any]] = []
    for ordinal, raw_fact in enumerate(payload["facts"]):
        try:
            if not isinstance(raw_fact, dict):
                raise ValueError(f"{provider} fact {ordinal} is not an object")
            fact = _comparison_fact(raw_fact, registry)
            relation_scope, field_evidence = _validate_field_grounding(provider, ordinal, raw_fact, fact, registry, identity["card_name"])
            is_table = relation_scope["scope_type"] == "table_row"
            source_ranges = tuple(sorted(
                (field if is_table else "", fragment["line_id"], fragment["char_start"], fragment["char_end"])
                for field, item in field_evidence.items()
                if is_table or field in {"benefit_type", "action", "target", "value", "unit"}
                for fragment in item.get("fragments", [])
            ))
            # Distinct source rows may share a benefit heading but have different
            # condition/cap pairs. Identical rows/ranges must still be rejected.
            row_anchor = tuple(raw_fact["relation_scope"]["row_line_ids"]) if is_table else ()
            fingerprint = (relation_scope["scope_type"], row_anchor, source_ranges)
            if fingerprint in scope_fingerprints:
                raise ValueError(f"{provider} two facts reuse one broad relation_scope")
            scope_fingerprints.add(fingerprint)
            validated.append({"fact": fact, "evidence": relation_scope["evidence"], "relation_scope": relation_scope, "field_evidence": field_evidence})
        except ValueError as error:
            # A grounding failure is not by itself proof of an incorrect OCR
            # value. Keep source references so a human can distinguish the two.
            supplied_scope = raw_fact.get("relation_scope") if isinstance(raw_fact, dict) else None
            category = "critical_content_mismatch" if isinstance(error, CriticalContentMismatch) else "verification_unresolved"
            fact_issues.append({"fact_index": ordinal, "category": category,
                                "error": str(error), "relation_scope": supplied_scope})
    if fact_issues:
        raise LaneFactReview(fact_issues, len(payload["facts"]))
    covered_line_ids = {line_id for item in validated for line_id in item["relation_scope"]["line_ids"]}
    identity_line_ids = {line_id for item in identity_evidence.values() for line_id in item.get("line_ids", [])}
    if "ignored_risky_lines" in payload:
        ignored = payload["ignored_risky_lines"]
        if not isinstance(ignored, list):
            raise ValueError(f"{provider} ignored_risky_lines is required")
        referenced = covered_line_ids | identity_line_ids
        ignored_ids: set[str] = set()
        for item in ignored:
            if not isinstance(item, dict):
                raise ValueError(f"{provider} ignored risky line is invalid")
            line_id = item.get("line_id")
            reason = normalized(item.get("reason", ""))
            if not isinstance(line_id, str) or line_id not in registry or line_id in ignored_ids or line_id in referenced or not reason:
                raise ValueError(f"{provider} ignored risky line is invalid")
            ignored_ids.add(line_id)
            line = registry[line_id][1]
            if not RISKY_IGNORED_LINE.search(line):
                raise ValueError(f"{provider} ignored risky line has no risky keyword")
            if not NON_BENEFIT_IGNORED_LINE.search(line) and not approved_layout_ignore(line, reason):
                raise LaneRestructureRequired(f"{provider} benefit-like ignored span requires a new structuring run")
        risky_unreferenced = {
            line_id
            for line_id, (_page, line) in registry.items()
            if line_id not in referenced and RISKY_IGNORED_LINE.search(line)
        }
        if risky_unreferenced != ignored_ids:
            raise LaneRestructureRequired(f"{provider} benefit-like OCR line lacks fact evidence or an approved ignore reason")
        critical_unreferenced = {
            line_id
            for line_id, (_page, line) in registry.items()
            if line_id not in referenced and _critical_source_line(line)
        }
        if critical_unreferenced - ignored_ids:
            raise ValueError(f"{provider} benefit condition, cap, exception, or value line lacks grounded relation scope")
        return validated
    # A v6 payload must make every risky source-line disposition explicit.  The
    # former page-quote/span-dispositions branch is diagnostic-only, never an
    # approval fallback.
    raise LaneRestructureRequired(f"{provider} ignored_risky_lines is required for v6 approval")


def validate_lanes_independently(payloads: dict[str, dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    validated: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, dict[str, Any]] = {}
    for provider in ("luna", "upstage"):
        try:
            validated[provider] = validate_lane(provider, payloads[provider])
        except LaneFactReview as error:
            errors[provider] = error.diagnostic()
        except LaneRestructureRequired as error:
            errors[provider] = {"status": "blocked", "error": str(error)}
        except (ValueError, FileNotFoundError) as error:
            errors[provider] = {"status": "review", "error": str(error)}
    return validated, errors


def diagnose_lane(provider: str, payload: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
    """Keep strict approval unchanged; finish independent diagnostics on rejection.

    A matching source fragment is NOT a grounded benefit relationship. Supplemental
    checks never return usable facts or replace the original rejection.
    """
    try:
        return validate_lane(provider, payload)
    except LaneFactReview as error:
        result = error.diagnostic()
    except LaneRestructureRequired as error:
        result = {"status": "blocked", "error": str(error)}
    except ValueError as error:
        result = {"status": "review", "error": str(error)}
    result["approval_eligible"] = False
    checks: list[dict[str, Any]] = []
    result["independent_checks"] = checks

    def record(check: str, status: str, *, error: str = "", **details: Any) -> None:
        checks.append({"check": check, "status": status, **({"error": error} if error else {}), **details})

    # Without an unambiguous source registry no source comparison is trustworthy.
    pages: dict[int, str] = {}
    try:
        if not isinstance(payload.get("pages"), list) or not payload["pages"]:
            raise ValueError("source pages are missing")
        for page in payload["pages"]:
            if not isinstance(page, dict):
                raise ValueError("source page is not an object")
            number, text = page.get("page", page.get("number")), page.get("text")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1 or number in pages or not isinstance(text, str):
                raise ValueError("source page number/text is invalid or duplicated")
            pages[number] = text
    except ValueError as error:
        record("source_checks", "not_checked", error=str(error))
        return result
    registry = _line_registry(pages)
    identity = payload.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    declared_ids: set[str] = set()
    for key, label in (("issuer", "issuer_name"), ("card", "card_name")):
        try:
            resolved = _resolve_evidence(provider, f"{key} identity", identity.get(f"{key}_evidence"), pages, registry)
            value = identity.get(label)
            if not isinstance(value, str) or not normalized(value) or normalized(value) not in resolved["quote"]:
                raise ValueError(f"{key} identity is not grounded")
            declared_ids.update(resolved.get("line_ids", []))
            record(f"identity.{key}", "source_match")
        except ValueError as error:
            record(f"identity.{key}", "review", error=str(error))

    facts = payload.get("facts")
    if not isinstance(facts, list):
        record("facts", "not_checked", error="facts is not an array")
        facts = []
    result["diagnosed_fact_count"] = len(facts)
    for ordinal, raw in enumerate(facts):
        if not isinstance(raw, dict):
            record("fact_fields", "not_checked", fact_index=ordinal, error="fact is not an object")
            continue
        fact_error = ""
        try:
            fact = _comparison_fact(raw, registry)
        except ValueError as error:
            fact_error = str(error)
            record("fact_schema", "review", fact_index=ordinal, error=fact_error)
            fact = {field: normalized(raw.get(field, "")) if isinstance(raw.get(field, ""), str) else "" for field in RELATION_FIELDS}
        scope = raw.get("relation_scope")
        if isinstance(scope, dict) and isinstance(scope.get("line_ids"), list):
            declared_ids.update(x for x in scope["line_ids"] if isinstance(x, str) and x in registry)
        # Run relationships even when the lane failed earlier at identity.
        try:
            if fact_error:
                raise ValueError(fact_error)
            _validate_field_grounding(provider, ordinal, raw, fact, registry, identity.get("card_name"))
            record("relationship", "checked", fact_index=ordinal)
        except ValueError as error:
            record("relationship", "review", fact_index=ordinal, error=str(error),
                   remaining_checks="not_checked_after_first_dependent_failure")

        supplied_fields = raw.get("field_evidence")
        supplied_fields = supplied_fields if isinstance(supplied_fields, dict) else {}
        source_fields: dict[str, str] = {}
        for field in RELATION_FIELDS:
            if not isinstance(raw.get(field, ""), str):
                record("field_comparison", "not_checked", fact_index=ordinal, field=field,
                       error="field value is not a string")
                continue
            supplied = supplied_fields.get(field)
            if not isinstance(supplied, list) or (fact[field] and not supplied) or (not fact[field] and supplied):
                record("field_source", "review", fact_index=ordinal, field=field,
                       error="field evidence array is missing or inconsistent with field value")
                continue
            fragments: list[tuple[str, int, int]] = []
            complete = True
            # Each fragment is independent too; collect every invalid location.
            for index, fragment in enumerate(supplied):
                try:
                    fragments.append(_resolve_field_fragment(fragment, registry))
                except ValueError as error:
                    complete = False
                    record("fragment", "review", fact_index=ordinal, field=field,
                           fragment_index=index, error=str(error))
            if complete:
                order = {key: index for index, key in enumerate(registry)}
                fragments = sorted(set(fragments), key=lambda x: (order[x[0]], x[1], x[2]))
                if any(a[0] == b[0] and a[2] > b[1] for a, b in zip(fragments, fragments[1:])):
                    record("field_comparison", "not_checked", fact_index=ordinal, field=field,
                           error="overlapping evidence prevents role comparison")
                    continue
                joined = " ".join(registry[key][1][start:end] for key, start, end in fragments)
                if field in {'condition', 'cap', 'frequency', 'period', 'exceptions'}:
                    joined = _strip_confirmed_list_labels(joined, registry, [x['line_id'] for x in supplied])
                source_fields[field] = normalized(joined)
            else:
                record("field_comparison", "not_checked", fact_index=ordinal, field=field,
                       error="one or more source fragments could not be resolved")

        for field in RELATION_FIELDS:
            if field not in source_fields:
                continue
            # Amount and unit are one comparison: 30만원 == 300000원.
            if field in {"value", "unit"} and not {"value", "unit"} <= source_fields.keys():
                record("field_comparison", "not_checked", fact_index=ordinal, field=field,
                       error="value/unit comparison requires both source fields")
                continue
            equal = field_comparison_key(source_fields, field) == field_comparison_key(fact, field)
            critical = field != "benefit_type" and (field in {"value", "unit"} or typed_literals(source_fields[field]) != typed_literals(fact[field]) or condition_operators(source_fields[field]) != condition_operators(fact[field]))
            record("field_comparison", "source_match" if equal else "review", fact_index=ordinal,
                   field=field, source=source_fields[field], value=fact[field],
                   category="source_match_only" if equal else "critical_content_mismatch" if critical else "verification_unresolved",
                   relationship_proven=False)

        # Bundle values are independently comparable even when the source's
        # table/heading relationship cannot be established. They never approve
        # a fact without the separate relationship check above.
        source_bundles, json_bundles = fact_bundles(source_fields), fact_bundles(fact)
        for group, fields in BUNDLE_FIELDS.items():
            required = set(fields)
            if group == "label_context":
                required.update(BUNDLE_FIELDS["benefit"])
            available = required <= source_fields.keys()
            equal = available and source_bundles[group] == json_bundles[group]
            record("value_bundle", "source_match" if equal else "review" if available else "not_checked",
                   fact_index=ordinal, bundle=group, fields=list(fields),
                   source=source_bundles[group] if available else None, value=json_bundles[group],
                   error="" if equal else "role values differ" if available else "source field evidence unavailable",
                   relationship_proven=False, approval_eligible=False)

    # Inventory declared references even if grounding failed. Do not mislabel
    # all rejected facts as omissions merely because they were not approved.
    ignored = payload.get("ignored_risky_lines")
    ignored_ids: set[str] = set()
    if not isinstance(ignored, list):
        record("ignored_source_lines", "review", error="ignored_risky_lines is not an array")
    else:
        for index, item in enumerate(ignored):
            line_id = item.get("line_id") if isinstance(item, dict) else None
            reason = item.get("reason") if isinstance(item, dict) else None
            if not isinstance(line_id, str) or line_id not in registry or line_id in ignored_ids or line_id in declared_ids or not isinstance(reason, str) or not normalized(reason):
                record("ignored_source_line", "review", item_index=index, error="invalid, duplicate or already referenced ignored line")
                continue
            ignored_ids.add(line_id)
            line = registry[line_id][1]
            if not RISKY_IGNORED_LINE.search(line) or not (NON_BENEFIT_IGNORED_LINE.search(line) or approved_layout_ignore(line, normalized(reason))):
                record("ignored_source_line", "review", item_index=index, line_id=line_id,
                       error="source line has no approved ignore reason")
    for line_id, (_page, line) in registry.items():
        if line_id not in declared_ids | ignored_ids and _critical_source_line(line):
            record("unclaimed_source_line", "review", line_id=line_id, source=line,
                   error="critical source line has no declared fact/identity/ignore reference")
    record("grounded_coverage", "not_checked", error="strict lane rejected; declared references are not proof of complete grounded coverage")
    return result


def validation_diagnostics(payloads: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Run four read-only validation jobs independently; callers persist on main thread."""
    tasks: dict[str, Callable[[], Any]] = {
        "ocr_comparison": lambda: compare_ocr_outputs(payloads["luna"], payloads["upstage"]),
        "luna_text_to_json": lambda: diagnose_lane("luna", payloads["luna"]),
        "upstage_text_to_json": lambda: diagnose_lane("upstage", payloads["upstage"]),
        "normalized_json_diagnostic_comparison": lambda: diagnostic_json_comparison(payloads["luna"], payloads["upstage"]),
    }
    outcomes: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="grounding") as executor:
        futures = {name: executor.submit(task) for name, task in tasks.items()}
        for name in tasks:
            try:
                outcomes[name] = futures[name].result()
            except LaneFactReview as error:
                outcomes[name] = error.diagnostic()
            except LaneRestructureRequired as error:
                outcomes[name] = {"status": "blocked", "error": str(error)}
            except Exception as error:  # Preserve every diagnostic failure; no task short-circuits another.
                outcomes[name] = {"status": "review", "error": f"{type(error).__name__}: {error}"}
    lanes = {
        provider: outcomes[f"{provider}_text_to_json"]
        for provider in ("luna", "upstage")
        if isinstance(outcomes[f"{provider}_text_to_json"], list)
    }
    errors = {
        provider: outcomes[f"{provider}_text_to_json"]
        for provider in ("luna", "upstage")
        if isinstance(outcomes[f"{provider}_text_to_json"], dict)
    }
    for name in ("ocr_comparison", "normalized_json_diagnostic_comparison"):
        outcome = outcomes[name]
        if isinstance(outcome, dict) and outcome.get("status") in {"blocked", "review"}:
            errors[name] = {"status": "review", "error": str(outcome.get("error", "diagnostic is not evaluable"))}
    return outcomes, lanes, errors


def _review_locations(payload: dict[str, Any], fact_index: int, field: str | None = None) -> list[dict[str, Any]]:
    """Resolve exact source ranges when possible; never invent a verified location."""
    facts = payload.get('facts', [])
    if not isinstance(facts, list) or not 0 <= fact_index < len(facts) or not isinstance(facts[fact_index], dict):
        return []
    raw = facts[fact_index]
    registry = _line_registry({p['page']: p['text'] for p in payload.get('pages', []) if isinstance(p, dict) and isinstance(p.get('page'), int) and isinstance(p.get('text'), str)})
    evidence = raw.get('field_evidence', {})
    fragments = evidence.get(field, []) if isinstance(evidence, dict) and field else []
    fragments = fragments if isinstance(fragments, list) else []
    result = []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        line_id = fragment.get('line_id')
        if not isinstance(line_id, str) or line_id not in registry:
            result.append({'line_id': line_id, 'location_verified': False, 'reason': 'source line unavailable'})
            continue
        page, text = registry[line_id]
        item = {'page': page, 'line_id': line_id, 'quote': text, 'location_verified': False}
        try:
            _, start, end = _resolve_field_fragment(fragment, registry)
            item.update(char_start=start, char_end=end, fragment=text[start:end], location_verified=True)
        except ValueError as error:
            item['reason'] = str(error)
        result.append(item)
    if not result:
        scope = raw.get('relation_scope', {})
        ids = scope.get('line_ids', []) if isinstance(scope, dict) else []
        for line_id in ids if isinstance(ids, list) else []:
            if isinstance(line_id, str) and line_id in registry:
                page, text = registry[line_id]
                result.append({'page': page, 'line_id': line_id, 'quote': text, 'location_verified': False,
                               'reason': 'declared scope only; exact field range unavailable'})
    return result


def diagnostic_json_comparison(luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> dict[str, Any]:
    def relations(payload: dict[str, Any]) -> tuple[Counter[tuple[str, ...]], list[str], dict[int, dict[str, Any]]]:
        keys: list[tuple[str, ...]] = []
        errors: list[str] = []
        entries: dict[int, dict[str, Any]] = {}
        registry = _line_registry({p['page']: p['text'] for p in payload.get('pages', []) if isinstance(p, dict) and isinstance(p.get('page'), int) and isinstance(p.get('text'), str)})
        facts = payload.get('facts')
        if not isinstance(facts, list) or not facts:
            return Counter(), ['non-empty facts array required'], entries
        for ordinal, fact in enumerate(facts):
            if isinstance(fact, dict):
                try:
                    entries[ordinal] = _comparison_fact(fact, registry)
                    keys.append(relation_tuple(entries[ordinal]))
                except ValueError as error:
                    errors.append(f"fact {ordinal}: {error}")
            else:
                errors.append(f"fact {ordinal}: not an object")
        return Counter(keys), errors, entries

    luna_relations, luna_errors, left = relations(luna_payload)
    upstage_relations, upstage_errors, right = relations(upstage_payload)
    result = {
        "purpose": "diagnostic_only_invalid_lanes_are_not_approval_eligible",
        "luna_only": sorted((luna_relations - upstage_relations).elements()),
        "upstage_only": sorted((upstage_relations - luna_relations).elements()),
        "shared_relation_count": sum((luna_relations & upstage_relations).values()),
        "comparison_eligible": not luna_errors and not upstage_errors and luna_payload.get("structure_schema_version") == STRUCTURE_SCHEMA_VERSION and upstage_payload.get("structure_schema_version") == STRUCTURE_SCHEMA_VERSION,
        "invalid_fact_count": {"luna": len(luna_errors), "upstage": len(upstage_errors)},
        "invalid_fact_errors": {"luna": luna_errors, "upstage": upstage_errors},
        "identity": {
            provider: {
                key: normalized(payload.get("identity", {}).get(key, ""))
                for key in ("issuer_name", "card_name")
            }
            for provider, payload in (("luna", luna_payload), ("upstage", upstage_payload))
        },
    }
    remaining = set(right)
    pending = []
    comparisons = []
    for i, fact in left.items():
        exact = [j for j in sorted(remaining) if relation_tuple(fact) == relation_tuple(right[j])]
        if exact:
            j = exact[0]
            remaining.remove(j)
            comparisons.append({'match': 'role_bundle_equivalent', 'luna_fact_index': i,
                                'upstage_fact_index': j, 'differences': []})
        else:
            pending.append(i)
    anchor = lambda fact: (field_comparison_key(fact, 'target'), field_comparison_key(fact, 'action'))
    candidates = {i: [j for j in sorted(remaining) if anchor(left[i]) == anchor(right[j])] for i in pending}
    paired = set()
    for i in pending:
        options = candidates[i]
        if len(options) == 1 and sum(options[0] in js for js in candidates.values()) == 1:
            j = options[0]
            paired.add(j)
            differences = []
            for field in RELATION_FIELDS:
                if field_comparison_key(left[i], field) != field_comparison_key(right[j], field):
                    conflicts = [other for other in ('condition', 'cap', 'frequency', 'period', 'exceptions')
                                 if other != field and field in ('condition', 'cap', 'frequency', 'period', 'exceptions')
                                 and left[i][field] and field_comparison_key(left[i], field) == field_comparison_key(right[j], other)]
                    differences.append({'field': field, 'luna': left[i][field], 'upstage': right[j][field],
                                        'reason': 'cross_field_role_conflict' if conflicts else 'field_difference',
                                        'possible_other_fields': conflicts,
                                        'luna_locations': _review_locations(luna_payload, i, field),
                                        'upstage_locations': _review_locations(upstage_payload, j, field)})
            comparisons.append({'match': 'paired_for_review', 'luna_fact_index': i, 'upstage_fact_index': j,
                                'candidate_count': 1, 'differences': differences,
                                'luna_bundles': fact_bundles(left[i]), 'upstage_bundles': fact_bundles(right[j])})
        else:
            comparisons.append({'match': 'ambiguous' if options else 'unmatched', 'luna_fact_index': i,
                                'upstage_fact_index': None, 'candidate_count': len(options),
                                'candidate_indices': options, 'luna_locations': _review_locations(luna_payload, i)})
    for j in sorted(remaining - paired):
        options = [i for i, js in candidates.items() if j in js]
        comparisons.append({'match': 'ambiguous' if options else 'unmatched', 'luna_fact_index': None,
                            'upstage_fact_index': j, 'candidate_count': len(options), 'candidate_indices': options,
                            'upstage_locations': _review_locations(upstage_payload, j)})
    identity_equal = result['identity']['luna'] == result['identity']['upstage'] and all(result['identity']['luna'].values())
    duplicates = {'luna': sum(n - 1 for n in luna_relations.values() if n > 1),
                  'upstage': sum(n - 1 for n in upstage_relations.values() if n > 1)}
    result.update(comparisons=comparisons, duplicate_relation_count=duplicates,
                  identity_equal=bool(identity_equal),
                  comparison_status='pass' if result['comparison_eligible'] and identity_equal and luna_relations == upstage_relations and not any(duplicates.values()) else 'review',
                  source_contracts={p: {'source_pdf_sha256': x.get('source_pdf_sha256'),
                                         'schema': x.get('structure_schema_version'), 'config_hash': x.get('provenance', {}).get('config_hash')}
                                    for p, x in (('luna', luna_payload), ('upstage', upstage_payload))})
    return result


def validation_summary(payloads: dict[str, dict[str, Any]], outcomes: dict[str, Any]) -> dict[str, Any]:
    """Presentation-neutral PDF verdict consumed by CLI, HTML or future admin UI."""
    own = {}
    for provider in ('luna', 'upstage'):
        outcome = outcomes[f'{provider}_text_to_json']
        if isinstance(outcome, list):
            own[provider] = {'status': 'pass', 'issues': []}
            continue
        issues = []
        # Keep content mismatches, unresolved relationships and unavailable checks separate.
        for check in outcome.get('independent_checks', []):
            if check['status'] not in {'review', 'not_checked'}:
                continue
            item = dict(check)
            item['kind'] = 'content_mismatch' if check.get('category') == 'critical_content_mismatch' else 'not_checked' if check['status'] == 'not_checked' else 'verification_unresolved'
            index = check.get('fact_index')
            if isinstance(index, int):
                item['locations'] = _review_locations(payloads[provider], index, check.get('field'))
                raw = payloads[provider]['facts'][index]
                raw = raw if isinstance(raw, dict) else {}
                item['benefit'] = {field: raw.get(field, '') for field in ('target', 'action', 'benefit_type')}
            elif isinstance(check.get('line_id'), str):
                item['locations'] = [{'line_id': check['line_id'], 'quote': check.get('source', ''), 'location_verified': False}]
            issues.append(item)
        own[provider] = {'status': outcome.get('status', 'review'), 'error': outcome.get('error'),
                         'issues': issues, 'approval_eligible': False}
    json_result = outcomes['normalized_json_diagnostic_comparison']
    json_status = json_result.get('comparison_status', 'review')
    own_status = 'blocked' if any(x['status'] == 'blocked' for x in own.values()) else 'review' if any(x['status'] != 'pass' for x in own.values()) else 'pass'
    ocr = outcomes['ocr_comparison']
    ocr_error = isinstance(ocr, dict) and ocr.get('status') in {'review', 'blocked'}
    return {'document_id': payloads['luna'].get('document_id'),
            'document_status': 'blocked' if own_status == 'blocked' else 'pass' if own_status == json_status == 'pass' and not ocr_error else 'review',
            'checks': {
                '1_json_to_json': {'status': json_status, 'grounding_confirmed': own_status == 'pass',
                                   'result': json_result},
                '2_ocr_to_json': {'status': own_status, 'providers': own},
                '3_ocr_to_ocr': {'status': 'not_checked' if ocr_error else 'same' if ocr.get('all_normalized_text_equal') else 'different',
                                  'auxiliary': True, 'result': ocr}},
            'note': 'OCR text differences alone do not reject a PDF. A source-match or review pairing never authorizes canonical output.'}


def canonical_from_lanes(luna: list[dict[str, Any]], upstage: list[dict[str, Any]], luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None, dict[str, Any] | None]:
    luna_keys = [relation_tuple(item["fact"]) for item in luna]
    upstage_keys = [relation_tuple(item["fact"]) for item in upstage]
    if len(luna_keys) != len(set(luna_keys)) or len(upstage_keys) != len(set(upstage_keys)):
        return None, {"error": "duplicate normalized relation is not approval eligible"}, None
    luna_by_tuple = dict(zip(luna_keys, luna))
    upstage_by_tuple = dict(zip(upstage_keys, upstage))
    if Counter(luna_keys) != Counter(upstage_keys):
        return None, {"luna_only": sorted((Counter(luna_keys) - Counter(upstage_keys)).elements()), "upstage_only": sorted((Counter(upstage_keys) - Counter(luna_keys)).elements())}, None
    luna_identity, upstage_identity = luna_payload["identity"], upstage_payload["identity"]
    identity_pair = (normalized(luna_identity["issuer_name"]), normalized(luna_identity["card_name"]))
    if identity_pair != (normalized(upstage_identity["issuer_name"]), normalized(upstage_identity["card_name"])):
        return None, None, {"luna": identity_pair, "upstage": (normalized(upstage_identity["issuer_name"]), normalized(upstage_identity["card_name"]))}
    canonical = [
        {
            "fact": luna_by_tuple[key]["fact"],
            "field_evidence_refs": {"luna": luna_by_tuple[key]["field_evidence"], "upstage": upstage_by_tuple[key]["field_evidence"]},
            "relation_scope_refs": {"luna": luna_by_tuple[key]["relation_scope"], "upstage": upstage_by_tuple[key]["relation_scope"]},
            "evidence_refs": {"luna": luna_by_tuple[key]["evidence"], "upstage": upstage_by_tuple[key]["evidence"]},
        }
        for key in sorted(luna_by_tuple)
    ]
    return canonical, None, None


def _identity_evidence(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    identity = payload["identity"]
    pages = {row["page"]: row["text"] for row in payload["pages"]}
    registry = _line_registry(pages)
    return {
        key: _resolve_evidence(provider, f"{key} identity", identity[f"{key}_evidence"], pages, registry)
        for key in ("issuer", "card")
    }


def _canonical_identity(luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "issuer_name": normalized(luna_payload["identity"]["issuer_name"]),
        "card_name": normalized(luna_payload["identity"]["card_name"]),
        "evidence_refs": {
            "luna": _identity_evidence("luna", luna_payload),
            "upstage": _identity_evidence("upstage", upstage_payload),
        },
    }


def strict_resolution(value: Any, luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("resolution"), dict) or not isinstance(value.get("canonical"), list) or not isinstance(value.get("identity"), dict):
        raise ValueError("resolution envelope requires resolution, identity, and canonical")
    resolution = value["resolution"]
    selected = resolution.get("selected_provider")
    selected_identity = resolution.get("selected_identity_provider")
    if selected not in {"luna", "upstage"} or selected_identity not in {"luna", "upstage"} or not isinstance(resolution.get("reason"), str) or not resolution["reason"].strip() or not isinstance(resolution.get("rejected_relations"), list):
        raise ValueError("resolution selected_provider, selected_identity_provider, reason, and rejected_relations are required")
    lanes = {"luna": validate_lane("luna", luna_payload), "upstage": validate_lane("upstage", upstage_payload)}
    by_tuple = {provider: {relation_tuple(item["fact"]): item for item in items} for provider, items in lanes.items()}
    if any(len(items) != len(by_tuple[provider]) for provider, items in lanes.items()):
        raise ValueError("duplicate normalized relations cannot be resolved")
    canonical: list[dict[str, Any]] = []
    canonical_keys: list[tuple[str, ...]] = []
    for item in value["canonical"]:
        if not isinstance(item, dict) or not isinstance(item.get("fact"), dict) or not isinstance(item.get("evidence_refs"), dict):
            raise ValueError("canonical item requires fact and evidence refs")
        fact = normalise_fact(item["fact"])
        key = relation_tuple(fact)
        if key not in by_tuple[selected]:
            raise ValueError("canonical fact is not an exact selected-provider relation")
        references: dict[str, Any] = {}
        for provider in ("luna", "upstage"):
            supplied = item["evidence_refs"].get(provider)
            if not isinstance(supplied, dict) or not isinstance(supplied.get("supports_selected"), bool):
                raise ValueError("each lane provenance requires supports_selected")
            candidates = by_tuple[provider]
            supported = key in candidates
            if supplied["supports_selected"] is not supported:
                raise ValueError("false supports_selected provenance")
            evidence = {key: value for key, value in supplied.items() if key != "supports_selected"}
            expected = candidates[key] if supported else next((candidate for candidate in candidates.values() if candidate["evidence"] == evidence), None)
            if expected is None or expected["evidence"] != evidence:
                raise ValueError("lane evidence is not an exact validated lane evidence")
            supplied_fields = item.get("field_evidence_refs", {})
            supplied_scope = item.get("relation_scope_refs", {})
            if supplied_fields and supplied_fields.get(provider) != expected["field_evidence"] or supplied_scope and supplied_scope.get(provider) != expected["relation_scope"]:
                raise ValueError("canonical field evidence or relation scope is not exact validated lane evidence")
            references[provider] = {**evidence, "supports_selected": supported}
        canonical.append({"fact": fact, "field_evidence_refs": {provider: by_tuple[provider][key]["field_evidence"] if key in by_tuple[provider] else next(candidate["field_evidence"] for candidate in by_tuple[provider].values() if candidate["evidence"] == {k: v for k, v in item["evidence_refs"][provider].items() if k != "supports_selected"}) for provider in ("luna", "upstage")}, "relation_scope_refs": {provider: by_tuple[provider][key]["relation_scope"] if key in by_tuple[provider] else next(candidate["relation_scope"] for candidate in by_tuple[provider].values() if candidate["evidence"] == {k: v for k, v in item["evidence_refs"][provider].items() if k != "supports_selected"}) for provider in ("luna", "upstage")}, "evidence_refs": references})
        canonical_keys.append(key)
    if len(canonical_keys) != len(set(canonical_keys)) or set(canonical_keys) != set(by_tuple[selected]):
        raise ValueError("canonical relations must exactly equal the selected-provider relation set")

    other = "upstage" if selected == "luna" else "luna"
    expected_rejected = set(by_tuple[other]) - set(by_tuple[selected])
    supplied_rejected: list[tuple[str, ...]] = []
    rejected_relations: list[dict[str, Any]] = []
    for rejected in resolution["rejected_relations"]:
        if not isinstance(rejected, dict) or set(rejected) != {"provider", "tuple", "reason"}:
            raise ValueError("each rejected relation requires exactly provider, tuple, and reason")
        raw_tuple = rejected["tuple"]
        expected_tuple_size = len(next(iter(by_tuple[other]), ()))
        if rejected["provider"] != other or not isinstance(raw_tuple, list) or len(raw_tuple) != expected_tuple_size or not all(isinstance(part, str) for part in raw_tuple) or not isinstance(rejected["reason"], str) or not rejected["reason"].strip():
            raise ValueError("rejected relation provider, tuple, or reason is invalid")
        key = tuple(raw_tuple)
        supplied_rejected.append(key)
        rejected_relations.append({"provider": other, "tuple": list(key), "reason": rejected["reason"].strip()})
    if len(supplied_rejected) != len(set(supplied_rejected)) or set(supplied_rejected) != expected_rejected:
        raise ValueError("rejected relations must exactly equal the non-selected relation difference")

    identities = {
        provider: (normalized(payload["identity"]["issuer_name"]), normalized(payload["identity"]["card_name"]))
        for provider, payload in (("luna", luna_payload), ("upstage", upstage_payload))
    }
    identity = value["identity"]
    if set(identity) != {"issuer_name", "card_name", "evidence_refs"} or not isinstance(identity["evidence_refs"], dict):
        raise ValueError("canonical identity requires issuer_name, card_name, and evidence_refs")
    selected_pair = identities[selected_identity]
    if (normalized(identity["issuer_name"]), normalized(identity["card_name"])) != selected_pair:
        raise ValueError("canonical identity is not the exact selected-provider identity")
    identity_refs: dict[str, Any] = {}
    for provider in ("luna", "upstage"):
        supplied = identity["evidence_refs"].get(provider)
        expected = _identity_evidence(provider, luna_payload if provider == "luna" else upstage_payload)
        supports_selected = identities[provider] == selected_pair
        if not isinstance(supplied, dict) or set(supplied) != {"issuer", "card", "supports_selected"} or supplied.get("supports_selected") is not supports_selected:
            raise ValueError("identity provenance requires exact supports_selected")
        if supplied.get("issuer") != expected["issuer"] or supplied.get("card") != expected["card"]:
            raise ValueError("identity evidence is not exact validated lane evidence")
        identity_refs[provider] = {**expected, "supports_selected": supports_selected}
    canonical_identity = {"issuer_name": selected_pair[0], "card_name": selected_pair[1], "evidence_refs": identity_refs}
    canonical = sorted(canonical, key=lambda item: relation_tuple(item["fact"]))
    audit = {
        "resolution": {"selected_provider": selected, "selected_identity_provider": selected_identity, "reason": resolution["reason"].strip(), "rejected_relations": rejected_relations},
        "identity": canonical_identity,
        "canonical": canonical,
    }
    return canonical, canonical_identity, audit


def read_source_manifest(path: Path) -> list[dict[str, str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    documents = value.get("documents") if isinstance(value, dict) else value
    if not isinstance(documents, list) or not documents:
        raise ValueError("source manifest must be a non-empty document list or object.documents")
    result = []
    for row in documents:
        source_value = row.get("source_pdf", row.get("path")) if isinstance(row, dict) else None
        if not isinstance(row, dict) or not isinstance(row.get("document_id"), str) or not isinstance(source_value, str):
            raise ValueError("source manifest document requires document_id and source_pdf/path")
        source = Path(source_value)
        if not source.is_absolute():
            candidates = [(parent / source).resolve() for parent in (path.parent, *path.parents)]
            matches = list(dict.fromkeys(candidate for candidate in candidates if candidate.is_file()))
            if len(matches) != 1:
                raise ValueError(f"source PDF path is missing or ambiguous: {source}")
            source = matches[0]
        if not source.is_file() or source.suffix.lower() != ".pdf":
            raise ValueError(f"source PDF unavailable: {source}")
        expected_hash = row.get("sha256")
        if expected_hash is not None and (not isinstance(expected_hash, str) or sha256_file(source) != expected_hash):
            raise ValueError(f"source PDF hash mismatch: {source}")
        result.append({"document_id": row["document_id"], "source_pdf": str(source)})
    if len({row["document_id"] for row in result}) != len(result):
        raise ValueError("source manifest document IDs must be unique")
    return sorted(result, key=lambda row: row["document_id"])


class Indexer:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.state = StateStore(runtime_root / "indexer-state.sqlite")

    def close(self) -> None:
        self.state.close()

    def run(
        self,
        source_manifest: Path,
        luna_dir: Path | None,
        upstage_dir: Path | None,
        *,
        fake_vectors: bool,
        allow_partial: bool,
        config: dict[str, Any],
        providers: dict[str, ProviderAdapter] | None = None,
        embedding_adapter: EmbeddingAdapter | None = None,
    ) -> dict[str, Any]:
        prepared = self.ocr(
            source_manifest,
            luna_dir,
            upstage_dir,
            config=config,
            providers=providers,
        )
        release_id = None
        if fake_vectors or embedding_adapter is not None:
            indexed = self.index(
                prepared["run_id"],
                allow_preview=allow_partial,
                fake_vectors=fake_vectors,
                profile=str(config.get("profile", config.get("strategy", DEFAULT_CHUNKING_PROFILE))),
                embedding_adapter=embedding_adapter,
            )
            release_id = indexed["release_id"]
        return {"run_id": prepared["run_id"], "release_id": release_id, "status": self.state.status(prepared["run_id"])}

    def ocr(
        self,
        source_manifest: Path,
        luna_dir: Path | None,
        upstage_dir: Path | None,
        *,
        config: dict[str, Any],
        providers: dict[str, ProviderAdapter] | Callable[[str, list[dict[str, str]]], dict[str, ProviderAdapter]] | None = None,
    ) -> dict[str, Any]:
        documents = read_source_manifest(source_manifest)
        input_hash = input_fingerprint(source_manifest, documents, luna_dir, upstage_dir)
        effective_config = {**config, "pipeline_contract": OCR_PIPELINE_CONTRACT}
        config_hash = sha256_bytes(canonical_json(effective_config).encode())
        run_id = "run_" + sha256_bytes(f"{input_hash}:{config_hash}".encode())[:16]
        run_id = self.state.find_or_create_run(run_id, input_hash, config_hash, now())
        active_providers = providers(run_id, documents) if callable(providers) else providers
        for document in documents:
            self._process_document(run_id, document, luna_dir, upstage_dir, active_providers)
        document_statuses = {str(row["status"]) for row in self.state.documents(run_id)}
        if "review" in document_statuses:
            self.state.set_run_status(run_id, "review", now())
        elif "blocked" in document_statuses:
            self.state.set_run_status(run_id, "blocked", now())
        elif document_statuses != {"canonical_approved"}:
            self.state.set_run_status(run_id, "failed", now())
        elif document_statuses == {"canonical_approved"}:
            self.state.set_run_status(run_id, "canonical_approved", now())
        status = self.state.status(run_id)
        differences = []
        for stage in status["stages"]:
            if stage["stage"] != "ocr_comparison":
                continue
            detail = json.loads(stage["detail_json"])
            if not detail.get("page_count_equal") or not detail.get("all_normalized_text_equal"):
                differences.append(stage["document_id"])
        return {"run_id": run_id, "ocr_difference_documents": differences, "status": status}

    def staged_ocr(
        self,
        source_manifest: Path,
        *,
        config: dict[str, Any],
        providers: dict[str, LiveLaneAdapter],
        stage: str,
    ) -> dict[str, Any]:
        if stage not in {"extract", "structure", "normalize", "validate"}:
            raise ValueError("OCR stage must be extract, structure, normalize, or validate")
        documents = read_source_manifest(source_manifest)
        input_hash = input_fingerprint(source_manifest, documents, None, None)
        effective_config = {**config, "pipeline_contract": OCR_PIPELINE_CONTRACT}
        config_hash = sha256_bytes(canonical_json(effective_config).encode())
        run_id = "run_" + sha256_bytes(f"{input_hash}:{config_hash}".encode())[:16]
        run_id = self.state.find_or_create_run(run_id, input_hash, config_hash, now())
        if set(providers) != {"luna", "upstage"} or any(providers[name].provider != name for name in providers):
            raise ValueError("exactly independent Luna and Upstage live adapters are required")

        source_hashes: dict[str, str] = {}
        preflight_failed = False
        for document in documents:
            document_id, source_path = document["document_id"], Path(document["source_pdf"])
            try:
                source_hash = sha256_file(source_path)
            except OSError as error:
                source_hash = f"unreadable:{type(error).__name__}"
                self.state.upsert_document(run_id, document_id, str(source_path), source_hash, "blocked")
                self.state.record_stage(run_id, document_id, "source", source_hash, "blocked", {"error": type(error).__name__}, now())
                preflight_failed = True
                continue
            source_hashes[document_id] = source_hash
            self.state.upsert_document(run_id, document_id, str(source_path), source_hash, "running")
            self.state.record_stage(run_id, document_id, "source", source_hash, "completed", {"source_sha256": source_hash}, now())
            self._record_json_artifact(run_id, document_id, "source", None, self._document_root(run_id, document_id) / "source.json", {"document_id": document_id, "source_pdf": str(source_path), "source_pdf_sha256": source_hash})
        if preflight_failed:
            self.state.set_run_status(run_id, "blocked", now())
            return {"run_id": run_id, "stage": stage, "status": self.state.status(run_id)}

        prerequisite = {"structure": "read_extracted", "normalize": "read_structured", "validate": "read_normalized"}.get(stage)
        if prerequisite:
            missing: list[tuple[str, str, str]] = []
            for document in documents:
                for provider, adapter in providers.items():
                    try:
                        getattr(adapter, prerequisite)(document["document_id"])
                    except (FileNotFoundError, RuntimeError, OcrProviderError) as error:
                        missing.append((document["document_id"], provider, str(error)))
            if missing:
                for document_id, provider, error in missing:
                    self.state.set_document_status(run_id, document_id, "blocked")
                    self.state.record_stage(run_id, document_id, stage, source_hashes[document_id], "blocked", {"provider": provider, "error": error, "action": f"complete previous stage before {stage}"}, now())
                self.state.set_run_status(run_id, "blocked", now())
                return {"run_id": run_id, "stage": stage, "status": self.state.status(run_id)}

        for document in documents:
            document_id = document["document_id"]
            source_hash = source_hashes[document_id]
            if stage in {"extract", "structure", "normalize"}:
                errors = []
                for provider, adapter in providers.items():
                    try:
                        if stage == "extract":
                            _path, envelope = adapter.extract(document_id)
                            self._materialize_extracted(run_id, document_id, provider, envelope, adapter)
                        elif stage == "structure":
                            _path, envelope = adapter.structure(document_id)
                            self._materialize_structured(run_id, document_id, provider, envelope, adapter)
                        else:
                            _path, payload = adapter.normalize(document_id)
                            self._materialize_lane(run_id, document_id, provider, payload, adapter)
                    except (OcrProviderError, FileNotFoundError, RuntimeError, ValueError) as error:
                        self._record_adapter_artifacts(run_id, document_id, provider, adapter)
                        errors.append({"provider": provider, "error": str(error), "retryable": isinstance(error, OcrProviderError) and error.retryable})
                if errors:
                    self.state.set_document_status(run_id, document_id, "blocked")
                    self.state.record_stage(run_id, document_id, stage, source_hash, "blocked", {"errors": errors}, now(), retryable=any(row["retryable"] for row in errors))
                elif stage == "extract":
                    self.state.set_document_status(run_id, document_id, "ocr_extracted")
                    self.state.record_stage(run_id, document_id, "ocr_extract", source_hash, "completed", {"providers": ["luna", "upstage"]}, now())
                elif stage == "structure":
                    self.state.set_document_status(run_id, document_id, "structured")
                    self.state.record_stage(run_id, document_id, "structure", source_hash, "completed", {"providers": ["luna", "upstage"]}, now())
                else:
                    self.state.set_document_status(run_id, document_id, "normalized")
                    self.state.record_stage(run_id, document_id, "normalize", source_hash, "completed", {"providers": ["luna", "upstage"]}, now())
            else:
                try:
                    luna_path, luna_payload = providers["luna"].read_normalized(document_id)
                    upstage_path, upstage_payload = providers["upstage"].read_normalized(document_id)
                    self._validate_staged_document(run_id, document_id, source_hash, luna_path, luna_payload, upstage_path, upstage_payload)
                except (OcrProviderError, FileNotFoundError, RuntimeError, ValueError) as error:
                    self.state.set_document_status(run_id, document_id, "blocked")
                    retryable = isinstance(error, OcrProviderError) and error.retryable
                    self.state.record_stage(run_id, document_id, stage, source_hash, "blocked", {"error": str(error)}, now(), retryable=retryable)

        expected = {"extract": "ocr_extracted", "structure": "structured", "normalize": "normalized"}.get(stage)
        statuses = {str(row["status"]) for row in self.state.documents(run_id)}
        if "blocked" in statuses:
            run_status = "blocked"
        elif "review" in statuses:
            run_status = "review"
        elif statuses == {"canonical_approved"}:
            run_status = "canonical_approved"
        elif expected and statuses == {expected}:
            run_status = expected
        else:
            run_status = "failed"
        self.state.set_run_status(run_id, run_status, now())
        return {"run_id": run_id, "stage": stage, "status": self.state.status(run_id)}

    def orchestrated_ocr(
        self,
        source_manifest: Path,
        *,
        config: dict[str, Any],
        providers: dict[str, LiveLaneAdapter],
    ) -> dict[str, Any]:
        completed = []
        result: dict[str, Any] = {}
        expected = {"extract": "ocr_extracted", "structure": "structured", "normalize": "normalized"}
        next_stage = {"extract": "structure", "structure": "normalize", "normalize": "validate"}
        for stage in ("extract", "structure", "normalize", "validate"):
            result = self.staged_ocr(source_manifest, config=config, providers=providers, stage=stage)
            completed.append(stage)
            if stage != "validate" and result["status"]["run"]["status"] != expected[stage]:
                return {**result, "completed_stages": completed, "stopped_before": next_stage[stage]}
        return {**result, "completed_stages": completed}

    def _record_adapter_artifacts(self, run_id: str, document_id: str, provider: str, adapter: LiveLaneAdapter) -> None:
        for kind, path in adapter.artifact_paths(document_id).items():
            if path.is_file():
                self.state.record_artifact(run_id, document_id, kind, provider, str(path), sha256_file(path), {})

    def _materialize_extracted(self, run_id: str, document_id: str, provider: str, envelope: dict[str, Any], adapter: LiveLaneAdapter) -> None:
        root = self._document_root(run_id, document_id) / provider
        pages = [dict(row) for row in envelope["pages"]]
        self._record_json_artifact(run_id, document_id, "ocr_pages", provider, root / "pages.json", {"document_id": document_id, "provider": provider, "pages": pages})
        text = pages_text(pages).encode("utf-8")
        write_immutable(root / "ocr.txt", text)
        self.state.record_artifact(run_id, document_id, "ocr_text", provider, str(root / "ocr.txt"), sha256_bytes(text), {})
        self._record_adapter_artifacts(run_id, document_id, provider, adapter)

    def _materialize_structured(self, run_id: str, document_id: str, provider: str, envelope: dict[str, Any], adapter: LiveLaneAdapter) -> None:
        root = self._document_root(run_id, document_id) / provider
        self._record_json_artifact(run_id, document_id, "structured_json", provider, root / "structured.json", envelope)
        self._record_adapter_artifacts(run_id, document_id, provider, adapter)

    def _validate_staged_document(
        self,
        run_id: str,
        document_id: str,
        source_hash: str,
        luna_path: Path,
        luna_payload: dict[str, Any],
        upstage_path: Path,
        upstage_payload: dict[str, Any],
    ) -> None:
        try:
            if luna_path.resolve() == upstage_path.resolve() or sha256_file(luna_path) == sha256_file(upstage_path):
                raise ValueError("provider artifacts must be distinct")
            if luna_payload.get("source_pdf_sha256") != source_hash or upstage_payload.get("source_pdf_sha256") != source_hash:
                raise ValueError("provider artifact source hash mismatch")
            outcomes, lanes, errors = validation_diagnostics({"luna": luna_payload, "upstage": upstage_payload})
            ocr_comparison = outcomes["ocr_comparison"]
            self._validation_artifact(run_id, document_id, "ocr_comparison", ocr_comparison)
            self.state.record_stage(run_id, document_id, "ocr_comparison", sha256_bytes(canonical_json(ocr_comparison).encode()), "completed" if not isinstance(ocr_comparison, dict) or "status" not in ocr_comparison else "review", ocr_comparison, now())
            diagnostic = outcomes["normalized_json_diagnostic_comparison"]
            self._validation_artifact(run_id, document_id, "normalized_json_diagnostic_comparison", diagnostic)
            self._validation_artifact(run_id, document_id, "validation_summary",
                                      validation_summary({'luna': luna_payload, 'upstage': upstage_payload}, outcomes))
            for provider in ("luna", "upstage"):
                result = errors.get(provider) or {"status": "pass", "facts": len(lanes[provider])}
                self._validation_artifact(run_id, document_id, f"{provider}_text_to_json", {**result, "source_pdf_sha256": source_hash})
            if errors:
                detail = {"lanes": errors}
                if any(error["status"] == "blocked" for error in errors.values()):
                    self.state.set_document_status(run_id, document_id, "blocked")
                    self._validation_artifact(run_id, document_id, "restructure_required", {"status": "blocked", **detail})
                    self.state.record_stage(run_id, document_id, "validate", source_hash, "blocked", {**detail, "action": "new structuring run"}, now())
                else:
                    signature = sha256_bytes(canonical_json(detail).encode())
                    self.state.open_review(run_id, document_id, "grounding_or_rule", signature, detail)
                    self.state.set_document_status(run_id, document_id, "review")
                    self._validation_artifact(run_id, document_id, "grounding_failure", {"status": "review", **detail})
                    self.state.record_stage(run_id, document_id, "validate", source_hash, "review", detail, now())
                return
            luna, upstage = lanes["luna"], lanes["upstage"]
            self.state.record_stage(run_id, document_id, "grounding", sha256_bytes(canonical_json([luna, upstage]).encode()), "completed", {"lanes": ["luna", "upstage"]}, now())
        except LaneRestructureRequired as error:
            self.state.set_document_status(run_id, document_id, "blocked")
            self._validation_artifact(run_id, document_id, "restructure_required", {"status": "blocked", "error": str(error)})
            self.state.record_stage(run_id, document_id, "validate", source_hash, "blocked", {"error": str(error), "action": "new structuring run"}, now())
            return
        except (ValueError, FileNotFoundError) as error:
            signature = sha256_bytes(str(error).encode())
            self.state.open_review(run_id, document_id, "grounding_or_rule", signature, {"error": str(error)})
            self.state.set_document_status(run_id, document_id, "review")
            self._validation_artifact(run_id, document_id, "grounding_failure", {"status": "review", "error": str(error)})
            self.state.record_stage(run_id, document_id, "validate", source_hash, "review", {"error": str(error)}, now())
            return
        canonical, mismatch, identity_mismatch = canonical_from_lanes(luna, upstage, luna_payload, upstage_payload)
        comparison = {"status": "review" if mismatch or identity_mismatch else "pass", "relation_mismatch": mismatch, "identity_mismatch": identity_mismatch}
        self._validation_artifact(run_id, document_id, "normalized_json_comparison", comparison)
        if identity_mismatch:
            signature = sha256_bytes(canonical_json(identity_mismatch).encode())
            self.state.open_review(run_id, document_id, "identity_mismatch", signature, identity_mismatch)
            self.state.set_document_status(run_id, document_id, "review")
            self.state.record_stage(run_id, document_id, "validate", signature, "review", identity_mismatch, now())
            return
        if mismatch:
            signature = sha256_bytes(canonical_json(mismatch).encode())
            self.state.open_review(run_id, document_id, "relation_mismatch", signature, mismatch)
            self.state.set_document_status(run_id, document_id, "review")
            self.state.record_stage(run_id, document_id, "validate", signature, "review", mismatch, now())
            return
        self._write_canonical(
            run_id,
            document_id,
            canonical,
            _canonical_identity(luna_payload, upstage_payload),
            structure_provider="upstage",
        )

    def index(
        self,
        run_id: str,
        *,
        allow_preview: bool,
        fake_vectors: bool,
        profile: str = DEFAULT_CHUNKING_PROFILE,
        embedding_adapter: EmbeddingAdapter | None = None,
    ) -> dict[str, Any]:
        release_id = self.publish(
            run_id,
            allow_partial=allow_preview,
            fake_vectors=fake_vectors,
            profile=profile,
            embedding_adapter=embedding_adapter,
        )
        manifest = json.loads((Path(str(self.state.release(release_id)["path"])) / "manifest.json").read_text(encoding="utf-8"))
        self.state.set_run_status(run_id, f"{manifest['release_status']}_published", now())
        return {"run_id": run_id, "release_id": release_id, "status": self.state.status(run_id)}

    def _document_root(self, run_id: str, document_id: str) -> Path:
        return self.runtime_root / "working" / run_id / "documents" / document_id.replace("/", "__")

    def _record_json_artifact(self, run_id: str, document_id: str, kind: str, provider: str | None, path: Path, value: Any) -> Path:
        encoded = (canonical_json(value) + "\n").encode()
        write_immutable(path, encoded)
        self.state.record_artifact(run_id, document_id, kind, provider, str(path), sha256_bytes(encoded), {})
        return path

    def _materialize_lane(self, run_id: str, document_id: str, provider: str, payload: dict[str, Any], adapter: ProviderAdapter) -> None:
        root = self._document_root(run_id, document_id) / provider
        pages = [dict(row) for row in payload.get("pages", [])]
        self._record_json_artifact(run_id, document_id, "ocr_pages", provider, root / "pages.json", {"document_id": document_id, "provider": provider, "pages": pages})
        text = pages_text(pages).encode("utf-8")
        write_immutable(root / "ocr.txt", text)
        self.state.record_artifact(run_id, document_id, "ocr_text", provider, str(root / "ocr.txt"), sha256_bytes(text), {})
        self._record_json_artifact(run_id, document_id, "normalized_json", provider, root / "normalized.json", payload)
        artifact_paths = getattr(adapter, "artifact_paths", None)
        if callable(artifact_paths):
            for kind, path in artifact_paths(document_id).items():
                if path.is_file():
                    self.state.record_artifact(run_id, document_id, kind, provider, str(path), sha256_file(path), {})

    def _validation_artifact(self, run_id: str, document_id: str, name: str, value: Any) -> Path:
        return self._record_json_artifact(
            run_id,
            document_id,
            name,
            None,
            self._document_root(run_id, document_id) / "validation" / f"{name}.json",
            value,
        )

    def _process_document(self, run_id: str, document: dict[str, str], luna_dir: Path | None, upstage_dir: Path | None, providers: dict[str, ProviderAdapter] | None) -> None:
        document_id, source_path = document["document_id"], Path(document["source_pdf"])
        try:
            source_hash = sha256_file(source_path)
        except OSError as error:
            source_hash = f"unreadable:{type(error).__name__}"
            self.state.upsert_document(run_id, document_id, str(source_path), source_hash, "blocked")
            self.state.record_stage(run_id, document_id, "source", source_hash, "blocked", {"error": type(error).__name__}, now(), retryable=False)
            return
        try:
            existing = self.state.document(run_id, document_id)
            if existing["source_hash"] == source_hash and existing["status"] == "canonical_approved":
                return
        except KeyError:
            pass
        self.state.upsert_document(run_id, document_id, str(source_path), source_hash, "running")
        self.state.record_stage(run_id, document_id, "source", source_hash, "completed", {"source_sha256": source_hash}, now())
        self._record_json_artifact(run_id, document_id, "source", None, self._document_root(run_id, document_id) / "source.json", {"document_id": document_id, "source_pdf": str(source_path), "source_pdf_sha256": source_hash})
        if providers is None and (luna_dir is None or upstage_dir is None):
            self.state.set_document_status(run_id, document_id, "blocked")
            self.state.record_stage(run_id, document_id, "ocr", source_hash, "blocked", {"reason": "local dual OCR artifacts required; live adapters fail closed"}, now())
            return
        adapters = providers or {"luna": LocalJsonAdapter("luna", luna_dir), "upstage": LocalJsonAdapter("upstage", upstage_dir)}
        try:
            if set(adapters) != {"luna", "upstage"} or adapters["luna"].provider != "luna" or adapters["upstage"].provider != "upstage":
                raise ValueError("exactly independent luna and upstage adapters are required")
            luna_path, luna_payload = adapters["luna"].load(document_id)
            upstage_path, upstage_payload = adapters["upstage"].load(document_id)
            if luna_path.resolve() == upstage_path.resolve() or sha256_file(luna_path) == sha256_file(upstage_path):
                raise ValueError("provider artifacts must be distinct")
            if luna_payload.get("source_pdf_sha256") != source_hash or upstage_payload.get("source_pdf_sha256") != source_hash:
                raise ValueError("provider artifact source hash mismatch")
            # Each lane is loaded and validated only from its own provider directory.
            self.state.record_artifact(run_id, document_id, "ocr_json", "luna", str(luna_path), sha256_file(luna_path), {"provider": "luna"})
            self.state.record_artifact(run_id, document_id, "ocr_json", "upstage", str(upstage_path), sha256_file(upstage_path), {"provider": "upstage"})
            self._materialize_lane(run_id, document_id, "luna", luna_payload, adapters["luna"])
            self._materialize_lane(run_id, document_id, "upstage", upstage_payload, adapters["upstage"])
            outcomes, lanes, errors = validation_diagnostics({"luna": luna_payload, "upstage": upstage_payload})
            ocr_comparison = outcomes["ocr_comparison"]
            self._validation_artifact(run_id, document_id, "ocr_comparison", ocr_comparison)
            self.state.record_stage(
                run_id,
                document_id,
                "ocr_comparison",
                sha256_bytes(canonical_json(ocr_comparison).encode()),
                "completed",
                ocr_comparison,
                now(),
            )
            diagnostic = outcomes["normalized_json_diagnostic_comparison"]
            self._validation_artifact(run_id, document_id, "normalized_json_diagnostic_comparison", diagnostic)
            self._validation_artifact(run_id, document_id, "validation_summary",
                                      validation_summary({'luna': luna_payload, 'upstage': upstage_payload}, outcomes))
            for provider in ("luna", "upstage"):
                result = errors.get(provider) or {"status": "pass", "facts": len(lanes[provider])}
                self._validation_artifact(run_id, document_id, f"{provider}_text_to_json", {**result, "source_pdf_sha256": source_hash})
            if errors:
                detail = {"lanes": errors}
                if any(error["status"] == "blocked" for error in errors.values()):
                    self.state.set_document_status(run_id, document_id, "blocked")
                    self._validation_artifact(run_id, document_id, "restructure_required", {"status": "blocked", **detail})
                    self.state.record_stage(run_id, document_id, "structured", source_hash, "blocked", {**detail, "action": "new_structuring_run"}, now(), retryable=False)
                else:
                    signature = sha256_bytes(canonical_json(detail).encode())
                    self.state.open_review(run_id, document_id, "grounding_or_rule", signature, detail)
                    self.state.set_document_status(run_id, document_id, "review")
                    self._validation_artifact(run_id, document_id, "grounding_failure", {"status": "review", **detail})
                    self.state.record_stage(run_id, document_id, "grounding", source_hash, "review", detail, now())
                return
            luna, upstage = lanes["luna"], lanes["upstage"]
            self.state.record_stage(run_id, document_id, "structured", sha256_bytes(canonical_json([luna, upstage]).encode()), "completed", {"lanes": ["luna", "upstage"]}, now())
            self.state.record_stage(run_id, document_id, "grounding", sha256_bytes(canonical_json([luna, upstage]).encode()), "completed", {"lanes": ["luna", "upstage"]}, now())
        except OcrProviderError as error:
            for provider, adapter in adapters.items():
                self._record_adapter_artifacts(run_id, document_id, provider, adapter)
            self.state.set_document_status(run_id, document_id, "blocked")
            self.state.record_stage(run_id, document_id, "ocr", source_hash, "blocked", {"error": str(error)}, now(), retryable=error.retryable)
            return
        except LaneRestructureRequired as error:
            self.state.set_document_status(run_id, document_id, "blocked")
            self._validation_artifact(run_id, document_id, "restructure_required", {"status": "blocked", "error": str(error)})
            self.state.record_stage(run_id, document_id, "structured", source_hash, "blocked", {"error": str(error), "action": "new_structuring_run"}, now(), retryable=False)
            return
        except (ValueError, FileNotFoundError) as error:
            signature = sha256_bytes(str(error).encode())
            self.state.open_review(run_id, document_id, "grounding_or_rule", signature, {"error": str(error)})
            self.state.set_document_status(run_id, document_id, "review")
            self._validation_artifact(run_id, document_id, "grounding_failure", {"status": "review", "error": str(error)})
            self.state.record_stage(run_id, document_id, "grounding", source_hash, "review", {"error": str(error)}, now())
            return
        canonical, mismatch, identity_mismatch = canonical_from_lanes(luna, upstage, luna_payload, upstage_payload)
        comparison = {"status": "review" if mismatch or identity_mismatch else "pass", "relation_mismatch": mismatch, "identity_mismatch": identity_mismatch}
        self._validation_artifact(run_id, document_id, "normalized_json_comparison", comparison)
        if identity_mismatch:
            signature = sha256_bytes(canonical_json(identity_mismatch).encode())
            self.state.open_review(run_id, document_id, "identity_mismatch", signature, identity_mismatch)
            self.state.set_document_status(run_id, document_id, "review")
            return
        if mismatch:
            signature = sha256_bytes(canonical_json(mismatch).encode())
            self.state.open_review(run_id, document_id, "relation_mismatch", signature, mismatch)
            self.state.set_document_status(run_id, document_id, "review")
            self.state.record_stage(run_id, document_id, "relation", signature, "review", mismatch, now())
            return
        self._write_canonical(
            run_id,
            document_id,
            canonical,
            _canonical_identity(luna_payload, upstage_payload),
            structure_provider="upstage",
        )

    def _write_canonical(
        self,
        run_id: str,
        document_id: str,
        canonical: list[dict[str, Any]],
        identity: dict[str, Any] | None = None,
        *,
        structure_provider: str,
    ) -> Path:
        if structure_provider not in {"luna", "upstage"}:
            raise ValueError("canonical structure provider is invalid")
        encoded = (canonical_json({"document_id": document_id, "identity": identity or {}, "facts": canonical, "structure_provider": structure_provider, "pipeline_contract": OCR_PIPELINE_CONTRACT, "chunking_contract": CHUNKING_CONTRACT}) + "\n").encode()
        canonical_sha256 = sha256_bytes(encoded)
        root = self.runtime_root / "working" / run_id / "canonical" / document_id.replace("/", "__")
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{canonical_sha256}.json"
        if path.exists():
            if sha256_file(path) != canonical_sha256:
                raise RuntimeError("content-addressed canonical path hash mismatch")
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
        self.state.approve_canonical(run_id, document_id, str(path), canonical_sha256, len(canonical), now())
        return path

    def resolve_review(self, review_id: int, reviewer: str, reason: str, after_path: Path, luna_dir: Path | None = None, upstage_dir: Path | None = None) -> dict[str, Any]:
        review = self.state.review(review_id)
        if review["status"] != "open":
            raise RuntimeError("review is not open")
        document = self.state.document(str(review["run_id"]), str(review["document_id"]))
        if (luna_dir is None) != (upstage_dir is None):
            raise ValueError("both local lane directories or neither are required")
        if luna_dir is None:
            luna_path = self.state.artifact_path(str(review["run_id"]), str(document["document_id"]), "ocr_json", "luna")
            upstage_path = self.state.artifact_path(str(review["run_id"]), str(document["document_id"]), "ocr_json", "upstage")
            luna_payload = json.loads(luna_path.read_text(encoding="utf-8"))
            upstage_payload = json.loads(upstage_path.read_text(encoding="utf-8"))
        else:
            luna_path, luna_payload = load_lane("luna", luna_dir, str(document["document_id"]))
            upstage_path, upstage_payload = load_lane("upstage", upstage_dir, str(document["document_id"]))
        if sha256_file(Path(str(document["source_path"]))) != document["source_hash"]:
            raise RuntimeError("stale source PDF")
        for provider, path, payload in (("luna", luna_path, luna_payload), ("upstage", upstage_path, upstage_payload)):
            if sha256_file(path) != self.state.artifact_hash(str(review["run_id"]), str(document["document_id"]), "ocr_json", provider):
                raise RuntimeError(f"stale {provider} artifact")
            if payload.get("source_pdf_sha256") != document["source_hash"]:
                raise RuntimeError(f"stale {provider} payload source hash")
        resolution = json.loads(after_path.read_text(encoding="utf-8"))
        canonical, identity, audit = strict_resolution(resolution, luna_payload, upstage_payload)
        structure_provider = audit["resolution"]["selected_provider"]
        encoded = (canonical_json({"document_id": document["document_id"], "identity": identity, "facts": canonical, "structure_provider": structure_provider, "pipeline_contract": OCR_PIPELINE_CONTRACT, "chunking_contract": CHUNKING_CONTRACT}) + "\n").encode()
        canonical_sha256 = sha256_bytes(encoded)
        root = self.runtime_root / "working" / str(review["run_id"]) / "canonical" / str(document["document_id"]).replace("/", "__")
        root.mkdir(parents=True, exist_ok=True)
        canonical_path = root / f"{canonical_sha256}.json"
        if canonical_path.exists():
            if sha256_file(canonical_path) != canonical_sha256:
                raise RuntimeError("content-addressed canonical path hash mismatch")
        else:
            descriptor = os.open(canonical_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
        approved = self.state.resolve_with_canonical(review_id, reviewer, reason, audit, str(canonical_path), canonical_sha256, len(canonical), now())
        return {"review_id": review_id, "canonical_path": str(canonical_path), "canonical_approved": approved, "luna_sha256": sha256_file(luna_path), "upstage_sha256": sha256_file(upstage_path)}

    @staticmethod
    def _chunk_record(
        document_id: str,
        identity: dict[str, Any],
        level: str,
        local_key: str,
        text: str,
        evidence_refs: Any,
        *,
        section: str | None = None,
        parent_id: str | None = None,
        child_ids: list[str] | None = None,
        source_pages: list[int] | None = None,
    ) -> dict[str, Any]:
        chunk_id = sha256_bytes(f"{document_id}:{level}:{local_key}:{text}".encode())[:32]
        path_title = " > ".join(str(value) for value in (identity.get("issuer_name"), identity.get("card_name"), level, section) if value)
        pages = evidence_pages(evidence_refs) if source_pages is None else sorted(set(source_pages))
        if not pages:
            raise ValueError("chunk requires source page provenance")
        reranker_text = f"[문서 경로] {path_title}\n[본문]\n{text}"
        return {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "level": level,
            "text": text,
            "metadata": {
                "document_id": document_id,
                "level": level,
                "issuer_name": identity.get("issuer_name"),
                "card_name": identity.get("card_name"),
                "section": section,
                "parent_id": parent_id,
                "child_ids": child_ids or [],
                "source_pages": pages,
                "retrieval_text": text,
                "reranker_text": reranker_text,
                "evidence_refs": evidence_refs,
                "related_chunk_ids": [],
            },
        }

    def _chunks(
        self,
        run_id: str,
        documents: Iterable[Any],
        profile: str = DEFAULT_CHUNKING_PROFILE,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if profile not in CHUNKING_PROFILES:
            raise ValueError(f"unsupported chunking profile: {profile}")
        chunks: list[dict[str, Any]] = []
        document_ids: list[str] = []
        for document in documents:
            document_id = str(document["document_id"])
            canonical_path = Path(str(document["canonical_path"]))
            if not document["canonical_sha256"] or sha256_file(canonical_path) != document["canonical_sha256"] or self.state.artifact_hash(run_id, document_id, "canonical", None) != document["canonical_sha256"]:
                raise RuntimeError("canonical artifact hash mismatch")
            payload = json.loads(canonical_path.read_text(encoding="utf-8"))
            if payload.get("pipeline_contract") != OCR_PIPELINE_CONTRACT or payload.get("chunking_contract") != CHUNKING_CONTRACT:
                raise RuntimeError("legacy canonical is not eligible for v6 chunking")
            identity = payload.get("identity", {})
            document_ids.append(document_id)
            facts = list(payload["facts"])
            if not facts:
                continue
            provider = payload.get("structure_provider")
            if provider not in {"luna", "upstage"}:
                raise RuntimeError("chunking requires an approved source provider")
            lane_path = self.state.artifact_path(run_id, document_id, "normalized_json", provider)
            if sha256_file(lane_path) != self.state.artifact_hash(run_id, document_id, "normalized_json", provider):
                raise RuntimeError("chunking source hash mismatch")
            lane = json.loads(lane_path.read_text(encoding="utf-8"))
            registry = _line_registry({row["page"]: row["text"] for row in lane["pages"]})
            source_order = {line_id: ordinal for ordinal, line_id in enumerate(registry)}
            facts.sort(key=lambda item: min(
                (source_order.get(line_id, len(registry)) for line_id in item.get("relation_scope_refs", {}).get(provider, {}).get("line_ids", [])),
                default=len(registry),
            ))
            if profile == "parent_child_bundle":
                raw = render_pages(lane.get("pages", []))
                structural, _hierarchy, audit = build_structural_chunks(
                    raw,
                    document_id=document_id,
                    issuer=str(identity.get("issuer_name", "")),
                    card_name=str(identity.get("card_name", "")),
                )
                if audit["heading_lines"] < 1:
                    raise RuntimeError("parent-child structure source has no Markdown headings")
                approved_scopes = []
                for item in facts:
                    scope = item.get("relation_scope_refs", {}).get(provider, {})
                    quote = scope.get("evidence", {}).get("quote") if isinstance(scope, dict) else None
                    if not isinstance(quote, str) or not quote or not any(normalized(quote) in normalized(chunk["text"]) for chunk in structural):
                        raise RuntimeError("parent-child chunking lost an approved canonical source scope")
                    approved_scopes.append(quote)
                for chunk in structural:
                    chunk["metadata"]["canonical_scope_coverage"] = [quote for quote in approved_scopes if normalized(quote) in normalized(chunk["text"])]
                chunks.extend(structural)
                continue
            card_text = " ".join(value for value in (identity.get("issuer_name"), identity.get("card_name")) if value)
            chunks.append(
                self._chunk_record(
                    document_id,
                    identity,
                    "card",
                    "card",
                    card_text,
                    identity.get("evidence_refs") or facts[0]["evidence_refs"],
                )
            )
            grouped: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
            pages: dict[int, list[tuple[int, dict[str, Any], str]]] = {}
            heading_by_line: dict[str, str] = {}
            headings: list[tuple[int, str]] = []
            for line_id, (page_number, source_line) in registry.items():
                match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", source_line)
                if match:
                    depth = len(match[1])
                    headings = [item for item in headings if item[0] < depth]
                    headings.append((depth, match[2]))
                heading_by_line[line_id] = " > ".join(item[1] for item in headings) or f"페이지 {page_number}"
            for index, item in enumerate(facts):
                selected_scope = item.get("relation_scope_refs", {}).get(provider)
                if not isinstance(selected_scope, dict) or not isinstance(selected_scope.get("evidence"), dict):
                    raise RuntimeError("canonical fact lacks approved source relation_scope")
                line_ids = selected_scope.get("line_ids", [])
                if not line_ids or any(line_id not in registry for line_id in line_ids):
                    raise RuntimeError("canonical source scope has invalid OCR line IDs")
                text = "\n".join(registry[line_id][1] for line_id in line_ids)
                if normalized(text) != normalized(selected_scope["evidence"].get("quote", "")):
                    raise RuntimeError("canonical source scope does not match its OCR lines")
                source_heading = heading_by_line[line_ids[0]]
                grouped.setdefault(source_heading, []).append((index, item, text))
                for page in sorted({registry[line_id][0] for line_id in line_ids}):
                    page_text = "\n".join(registry[line_id][1] for line_id in line_ids if registry[line_id][0] == page)
                    pages.setdefault(page, []).append((index, item, page_text))
            if profile == "card_page_section_benefit":
                for page, rows in pages.items():
                    chunks.append(
                        self._chunk_record(
                            document_id,
                            identity,
                            "page",
                            str(page),
                            "\n".join(row[2] for row in rows),
                            [row[1]["evidence_refs"] for row in rows],
                            source_pages=[page],
                        )
                    )
                for section, rows in grouped.items():
                    child_ids = [
                        sha256_bytes(f"{document_id}:benefit:{index}:{text}".encode())[:32]
                        for index, _item, text in rows
                    ]
                    section_record = self._chunk_record(
                        document_id,
                        identity,
                        "section",
                        section,
                        "\n".join(row[2] for row in rows),
                        [row[1]["evidence_refs"] for row in rows],
                        section=section,
                        child_ids=child_ids,
                    )
                    chunks.append(section_record)
                    for index, item, text in rows:
                        chunks.append(self._chunk_record(
                            document_id, identity, "benefit", str(index), text, item["evidence_refs"], section=section, parent_id=section_record["chunk_id"]
                        ))
        return sorted(chunks, key=lambda item: item["chunk_id"]), sorted(document_ids)

    def publish(
        self,
        run_id: str,
        *,
        allow_partial: bool,
        fake_vectors: bool,
        profile: str = DEFAULT_CHUNKING_PROFILE,
        embedding_adapter: EmbeddingAdapter | None = None,
    ) -> str:
        if fake_vectors and embedding_adapter is not None:
            raise ValueError("fake vectors and an embedding adapter are mutually exclusive")
        if not fake_vectors and embedding_adapter is None:
            raise RuntimeError("embedding is blocked: inject an approved adapter or use explicit test-only --fake-vectors")
        all_documents = self.state.documents(run_id)
        approved = [row for row in all_documents if row["status"] == "canonical_approved"]
        omitted = [str(row["document_id"]) for row in all_documents if row["status"] != "canonical_approved"]
        if omitted and not allow_partial:
            raise RuntimeError("release blocked: documents are not canonical_approved")
        document_ids = [str(row["document_id"]) for row in approved]
        if not approved or self.state.unresolved_count(run_id, document_ids):
            raise RuntimeError("release blocked: unresolved review or no approved documents")
        chunks, document_ids = self._chunks(run_id, approved, profile)
        if not chunks:
            raise RuntimeError("release blocked: no benefit chunks")
        if fake_vectors:
            embedding_model = "test-only-sha256-16"
            dimension = 16
            release_status = "test_only"
            vector_mode = "test_fake_vector"
        else:
            embedding_model = str(getattr(embedding_adapter, "model", "")).strip()
            dimension = getattr(embedding_adapter, "dimension", 0)
            if not embedding_model or isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
                raise ValueError("embedding adapter model and dimension are required")
            release_status = "preview" if omitted else "production"
            vector_mode = "approved_adapter"
        corpus_hash = sha256_bytes(canonical_json(chunks).encode())
        release_id = "release_" + sha256_bytes(
            f"{run_id}:{corpus_hash}:{embedding_model}:{dimension}:{release_status}:{LEXICAL_CONTRACT}".encode()
        )[:16]
        final_root = self.runtime_root / "index-release" / release_id
        if final_root.exists():
            manifest = json.loads((final_root / "manifest.json").read_text(encoding="utf-8"))
            if (
                manifest.get("embedding_model") != embedding_model
                or manifest.get("embedding_dimension") != dimension
                or manifest.get("release_status") != release_status
                or manifest.get("lexical_contract") != LEXICAL_CONTRACT
            ):
                raise RuntimeError("existing release embedding contract mismatch")
            embeddings = self._read_embeddings(
                final_root / "chroma",
                profile,
                [chunk["chunk_id"] for chunk in chunks],
            )
            self._materialize_serving(final_root, manifest, chunks, embeddings)
            try:
                self.state.release(release_id)
            except KeyError:
                self.state.record_release(release_id, run_id, str(final_root), sha256_file(final_root / "manifest.json"), now(), str(manifest["release_status"]))
            return release_id
        final_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=final_root.parent))
        try:
            corpus_path = temporary_root / "corpus.sqlite"
            self._build_fts(corpus_path, chunks)
            retrieval_texts = [str(chunk["metadata"]["retrieval_text"]) for chunk in chunks]
            if fake_vectors:
                embeddings = np.asarray(
                    [self._fake_embedding(text, dimension) for text in retrieval_texts],
                    dtype=np.float32,
                )
                embedding_usage: dict[str, Any] = {
                    "provider_called": False,
                    "item_count": len(chunks),
                }
            else:
                adapter_embeddings, embedding_usage = embedding_adapter.embed_documents(retrieval_texts)
                embeddings = np.asarray(adapter_embeddings, dtype=np.float32)
                if not isinstance(embedding_usage, dict):
                    raise ValueError("embedding adapter usage must be a dictionary")
            if embeddings.shape != (len(chunks), dimension) or not np.isfinite(embeddings).all():
                raise ValueError("embedding adapter result shape or finiteness mismatch")
            self._build_chroma(temporary_root / "chroma", chunks, embeddings, corpus_hash, profile)
            manifest = {
                "schema_version": "rag_index_release_v1",
                "release_id": release_id,
                "run_id": run_id,
                "strategy": profile,
                "chunking_contract": STRUCTURAL_CONTRACT if profile == "parent_child_bundle" else CHUNKING_CONTRACT,
                "lexical_contract": LEXICAL_CONTRACT,
                "vector_mode": vector_mode,
                "release_status": release_status,
                "distance_contract": "squared_l2",
                "corpus_hash": corpus_hash,
                "scope_document_ids": document_ids,
                "requested_document_ids": sorted(str(row["document_id"]) for row in all_documents),
                "document_ids": document_ids,
                "catalog": [{"document_id": key[0], "issuer_name": key[1], "card_name": key[2]} for key in sorted({(chunk["document_id"], chunk["metadata"].get("issuer_name"), chunk["metadata"].get("card_name")) for chunk in chunks})],
                "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
                "embedding_dimension": dimension,
                "embedding_model": embedding_model,
                "embedding_usage": embedding_usage,
                "embedding_sha256": embedding_sha256(
                    [chunk["chunk_id"] for chunk in chunks], embeddings
                ),
                "corpus_sqlite_sha256": sha256_file(corpus_path),
                "chroma_tree_sha256": tree_hash(temporary_root / "chroma"),
                "coverage": {"included_document_ids": document_ids, "omitted_document_ids": omitted, "partial": bool(omitted)},
            }
            manifest_path = temporary_root / "manifest.json"
            manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
            self._verify_release(temporary_root, manifest, chunks, embeddings)
            os.replace(temporary_root, final_root)
            self._make_read_only(final_root)
            self._materialize_serving(final_root, manifest, chunks, embeddings)
            self.state.record_release(
                release_id,
                run_id,
                str(final_root),
                sha256_file(final_root / "manifest.json"),
                now(),
                release_status,
            )
            return release_id
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise

    @staticmethod
    def _fake_embedding(text: str, dimension: int) -> np.ndarray:
        values = bytearray()
        counter = 0
        while len(values) < dimension:
            values.extend(hashlib.sha256(f"{counter}:{text}".encode()).digest())
            counter += 1
        return (np.frombuffer(bytes(values[:dimension]), dtype=np.uint8).astype(np.float32) / 255.0) - 0.5

    @staticmethod
    def _read_embeddings(path: Path, collection_name: str, chunk_ids: list[str]) -> np.ndarray:
        import chromadb

        with tempfile.TemporaryDirectory() as temporary:
            working = Path(temporary) / "chroma"
            shutil.copytree(path, working, copy_function=shutil.copy2)
            working.chmod(0o755)
            for item in working.rglob("*"):
                item.chmod(0o755 if item.is_dir() else 0o644)
            client = chromadb.PersistentClient(path=str(working))
            collection = client.get_collection(collection_name)
            output = collection.get(ids=chunk_ids, include=["embeddings"])
            by_id = {
                chunk_id: embedding
                for chunk_id, embedding in zip(output["ids"], output["embeddings"], strict=True)
            }
            if set(by_id) != set(chunk_ids):
                raise RuntimeError("stored embedding identity mismatch")
            embeddings = np.asarray([by_id[chunk_id] for chunk_id in chunk_ids], dtype=np.float32)
            del collection, client
            gc.collect()
        if embeddings.ndim != 2 or not np.isfinite(embeddings).all():
            raise RuntimeError("stored embeddings are invalid")
        return embeddings

    @staticmethod
    def _build_fts(path: Path, chunks: list[dict[str, Any]]) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.executescript(
                """CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL, metadata_json TEXT NOT NULL) STRICT;
                   CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61');"""
            )
            for chunk in chunks:
                connection.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", (chunk["chunk_id"], chunk["document_id"], chunk["level"], chunk["text"], canonical_json(chunk["metadata"])))
                connection.execute(
                    "INSERT INTO chunks_fts VALUES(?,?)",
                    (
                        chunk["chunk_id"],
                        " ".join(lexical_terms(str(chunk["metadata"].get("retrieval_text", chunk["text"])))),
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _build_chroma(
        path: Path,
        chunks: list[dict[str, Any]],
        embeddings: np.ndarray,
        corpus_hash: str = "test",
        collection_name: str = DEFAULT_CHUNKING_PROFILE,
    ) -> None:
        import chromadb

        client = chromadb.PersistentClient(path=str(path))
        collection = client.get_or_create_collection(collection_name, metadata={"hnsw:space": "l2", "corpus_hash": corpus_hash})
        collection.add(
            ids=[chunk["chunk_id"] for chunk in chunks],
            documents=[str(chunk["metadata"].get("retrieval_text", chunk["text"])) for chunk in chunks],
            metadatas=[{"document_id": chunk["document_id"], "level": chunk["level"]} for chunk in chunks],
            embeddings=embeddings.tolist(),
        )
        del collection, client
        gc.collect()

    @staticmethod
    def _verify_release(root: Path, manifest: dict[str, Any], chunks: list[dict[str, Any]], embeddings: np.ndarray, chroma_root: Path | None = None) -> None:
        connection = sqlite3.connect(root / "corpus.sqlite")
        try:
            rows = connection.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id").fetchall()
            fts_count = connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        finally:
            connection.close()
        expected_ids = [chunk["chunk_id"] for chunk in chunks]
        if [row[0] for row in rows] != expected_ids or fts_count != len(expected_ids):
            raise RuntimeError("FTS identity mismatch")
        if manifest.get("corpus_sqlite_sha256") and sha256_file(root / "corpus.sqlite") != manifest["corpus_sqlite_sha256"]:
            raise RuntimeError("SQLite corpus hash mismatch")
        import chromadb

        client = chromadb.PersistentClient(path=str(chroma_root or root / "chroma"))
        collection = client.get_collection(manifest.get("strategy", DEFAULT_CHUNKING_PROFILE))
        output = collection.get(include=["embeddings"])
        collection_corpus_hash = collection.metadata.get("corpus_hash")
        del collection, client
        gc.collect()
        output_by_id = {
            chunk_id: embedding
            for chunk_id, embedding in zip(output["ids"], output["embeddings"], strict=True)
        }
        stored_embeddings = np.asarray(
            [output_by_id[chunk_id] for chunk_id in expected_ids if chunk_id in output_by_id],
            dtype=np.float32,
        )
        if set(output_by_id) != set(expected_ids) or stored_embeddings.shape != embeddings.shape:
            raise RuntimeError("FTS5-Chroma identity or dimension mismatch")
        if manifest["chunk_ids"] != expected_ids or manifest["document_ids"] != sorted({chunk["document_id"] for chunk in chunks}):
            raise RuntimeError("release manifest identity mismatch")
        if collection_corpus_hash != manifest.get("corpus_hash", "test"):
            raise RuntimeError("Chroma corpus hash mismatch")
        if manifest.get("embedding_sha256") and embedding_sha256(expected_ids, stored_embeddings) != manifest["embedding_sha256"]:
            raise RuntimeError("Chroma embedding hash mismatch")
        if manifest.get("chroma_tree_sha256") and tree_hash(root / "chroma") != manifest["chroma_tree_sha256"]:
            raise RuntimeError("immutable Chroma source hash mismatch")

    def _materialize_serving(self, release_root: Path, manifest: dict[str, Any], chunks: list[dict[str, Any]], embeddings: np.ndarray) -> Path:
        source = release_root / "chroma"
        if tree_hash(source) != manifest["chroma_tree_sha256"]:
            raise RuntimeError("serving copy source hash mismatch")
        release_id = str(manifest["release_id"])
        version = str(manifest["chroma_tree_sha256"])
        if not re.fullmatch(r"[0-9a-f]{64}", version):
            raise RuntimeError("invalid Chroma tree hash")
        serving_root = self.runtime_root / "serving"
        version_parent = serving_root / release_id
        version_root = version_parent / version
        marker_contract = {
            "release_id": release_id,
            "chroma_tree_sha256": version,
            "corpus_hash": manifest["corpus_hash"],
            "chunk_ids": manifest["chunk_ids"],
            "embedding_dimension": manifest["embedding_dimension"],
            "embedding_sha256": manifest["embedding_sha256"],
        }
        lock_root = serving_root / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        with (lock_root / f"{release_id}.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if version_root.exists():
                    marker_path = version_root / "version.json"
                    existing_marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else None
                    if not isinstance(existing_marker, dict) or existing_marker != marker_contract:
                        raise RuntimeError("existing serving version marker mismatch")
                    stored = self._read_embeddings(
                        version_root / "chroma",
                        str(manifest["strategy"]),
                        list(manifest["chunk_ids"]),
                    )
                    if embedding_sha256(list(manifest["chunk_ids"]), stored) != manifest["embedding_sha256"]:
                        raise RuntimeError("existing serving version content mismatch")
                    return version_root
                version_parent.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=version_parent))
                try:
                    shutil.copytree(source, staging / "chroma", copy_function=shutil.copy2)
                    for path in (staging / "chroma").rglob("*"):
                        path.chmod(0o755 if path.is_dir() else 0o644)
                    (staging / "chroma").chmod(0o755)
                    self._verify_release(release_root, manifest, chunks, embeddings, staging / "chroma")
                    if tree_hash(source) != version:
                        raise RuntimeError("immutable Chroma source changed while materializing serving copy")
                    (staging / "version.json").write_text(canonical_json(marker_contract) + "\n", encoding="utf-8")
                    os.replace(staging, version_root)
                    (version_root / "version.json").chmod(0o444)
                    version_root.chmod(0o555)
                except Exception:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise
                return version_root
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _make_read_only(root: Path) -> None:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.chmod(0o444)
            elif path.is_dir():
                path.chmod(0o555)
        root.chmod(0o555)

    def activate(self, release_id: str) -> Path:
        import fcntl

        release = self.state.release(release_id)
        root = Path(str(release["path"]))
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("release_id") != release_id:
            raise RuntimeError("release manifest ID mismatch")
        if manifest.get("release_status") != "production":
            raise RuntimeError("only a production release can be activated")
        if manifest.get("lexical_contract") != LEXICAL_CONTRACT:
            raise RuntimeError("release lexical contract mismatch; rebuild before activation")
        if sha256_file(root / "manifest.json") != release["manifest_sha256"]:
            raise RuntimeError("release manifest hash mismatch")
        target = self.runtime_root / "active-index.json"
        lock_path = self.runtime_root / ".active-index.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            temporary = target.with_name(f".active-index.{os.getpid()}.tmp")
            temporary.write_text(canonical_json({"release_id": release_id, "manifest_sha256": sha256_file(root / "manifest.json")}) + "\n", encoding="utf-8")
            os.replace(temporary, target)
            self.state.record_activation(release_id, "activate", now())
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return target

    def rollback(self, release_id: str) -> Path:
        pointer = self.activate(release_id)
        self.state.record_activation(release_id, "rollback", now())
        return pointer
