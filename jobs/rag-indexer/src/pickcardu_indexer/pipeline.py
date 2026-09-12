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
)
from .structural import HEADING_RE, STRUCTURAL_CONTRACT, build_structural_chunks, render_pages


NUMBER = re.compile(r"\d+(?:[,.]\d+)?")
NUMBER_TOKEN = re.compile(r"(?<![\d.])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])")
SOURCE_UNIT = re.compile(r"\s*(?:만\s*원|천\s*원|원|%|％|퍼센트|마일리지|마일|포인트|개월|회|년|일|시간|분|초|점|달러)")
CLOCK_LITERAL = re.compile(r"(?<![\d:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])|(?<!\d)(?:(?:오전|오후)\s*(?:0?[1-9]|1[0-2])|(?:[01]?\d|2[0-3]))\s*시(?:\s*[0-5]?\d\s*분)?(?!간)")
DATE_LITERAL = re.compile(r"(?<![\d-])(\d{4})(?:\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일|-(\d{2})-(\d{2}))(?![\d-])")
PHONE_LITERAL = re.compile(r"(?<![\d-])(?:0\d{1,2}-\d{3,4}-\d{4}|1\d{3}-\d{4})(?![\d-])")
CONTACT_LABEL = re.compile(r"☎|전화|고객센터|고객상담|문의|연락처|팩스")
RISKY_IGNORED_LINE = re.compile(r"할인|적립|캐시백|마일|포인트|무료|면제|혜택")
BENEFIT_VERB_SIGNAL = re.compile(r"할인|적립|캐시백|무료|면제")
BENEFIT_NOUN_SUFFIX = re.compile(r"\s*(?:율|률|대상)")
BENEFIT_COMBINED_LABEL = re.compile(r"(?:할인/적립|적립/할인)")
CRITICAL_RELATION_LINE = re.compile(r"할인|적립|캐시백|마일|포인트|무료|면제|(?:전월|지난달).?실적|이용.?금액|한도|횟수|이상|초과|이하|미만|제외|불가|조건")
FIELD_ROLE_MARKERS = {
    "condition": re.compile(r"(?:전월|지난달).?실적|이용.?금액|이상|초과|이하|미만|조건"),
    "cap": re.compile(r"한도"),
    "frequency": re.compile(r"횟수|월\s*\d+회|일\s*\d+회|연\s*\d+회"),
    "period": re.compile(r"기간|개월|연간|월간"),
    "exceptions": re.compile(r"제외|불가|미적용"),
}
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
OCR_PIPELINE_CONTRACT = "dual-lane-field-evidence-relation-v10"


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


def _contains_range(container: tuple[str, int, int], line_id: str, start: int, end: int) -> bool:
    return container[0] == line_id and container[1] <= start and end <= container[2]


def _context_literal_ranges(text: str, field: str) -> list[tuple[int, int]]:
    """Recognize complete non-monetary tokens, never exempt their whole line."""
    if field not in {"condition", "period", "exceptions"}:
        return []
    ranges = []
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


def _scope_line_ids(provider: str, ordinal: int, scope: Any, registry: dict[str, tuple[int, str]]) -> list[str]:
    if not isinstance(scope, dict):
        raise ValueError(f"{provider} fact {ordinal} relation_scope is required")
    scope_type, line_ids = scope.get("scope_type"), scope.get("line_ids")
    if scope_type not in {"sentence", "bounded_block", "table_row"}:
        raise ValueError(f"{provider} fact {ordinal} relation_scope is ambiguous or unknown")
    if not isinstance(line_ids, list) or not line_ids or len(set(line_ids)) != len(line_ids) or not all(isinstance(line_id, str) and line_id in registry for line_id in line_ids):
        raise ValueError(f"{provider} fact {ordinal} relation_scope line_ids are invalid")
    order = {line_id: index for index, line_id in enumerate(registry)}
    ordered = sorted(line_ids, key=order.__getitem__)
    if scope_type == "sentence" and len(ordered) != 1:
        raise ValueError(f"{provider} fact {ordinal} sentence scope must be one source line")
    if scope_type == "bounded_block":
        if len({registry[line_id][0] for line_id in ordered}) != 1:
            raise ValueError(f"{provider} fact {ordinal} bounded block is ambiguous")
        selected = set(ordered)
        first, last = order[ordered[0]], order[ordered[-1]]
        skipped = [line_id for line_id in list(registry)[first:last + 1] if line_id not in selected]
        # OCR can mark an adjacent explanatory sentence as another '# heading'.
        # Use hierarchy only to guard a remote jump, not as a blanket rejection
        # of contiguous text already checked by the field/numeric validators.
        if skipped:
            _check_heading_chain(provider, ordinal, ordered, registry)
        if any(
            registry[line_id][1].strip().startswith("|")
            or CRITICAL_RELATION_LINE.search(registry[line_id][1]) and not _is_generic_layout_line(registry[line_id][1])
            for line_id in skipped
        ):
            raise ValueError(f"{provider} fact {ordinal} bounded block skips a competing relation boundary; shared context is not proven")
    header, row = scope.get("header_line_ids"), scope.get("row_line_ids")
    if not isinstance(header, list) or not isinstance(row, list):
        raise ValueError(f"{provider} fact {ordinal} relation_scope header/row arrays are required")
    if scope_type == "table_row":
        if len(header) != 1 or len(row) != 1:
            raise ValueError(f"{provider} fact {ordinal} table scope requires one header and one row")
        if not set(header + row) <= set(ordered) or header[0] not in registry or row[0] not in registry:
            raise ValueError(f"{provider} fact {ordinal} table scope does not match header and row")
        if len({registry[line_id][0] for line_id in ordered}) != 1 or order[header[0]] >= order[row[0]]:
            raise ValueError(f"{provider} fact {ordinal} table header must precede its row on the same page")
        registry_ids = list(registry)
        header_index, row_index = order[header[0]], order[row[0]]
        if any(not registry[line_id][1].strip().startswith("|") for line_id in registry_ids[header_index:row_index + 1]):
            raise ValueError(f"{provider} fact {ordinal} table header and row cross a non-table boundary")
        block_start, block_end = header_index, row_index
        while block_start and registry[registry_ids[block_start - 1]][1].strip().startswith("|"):
            block_start -= 1
        while block_end + 1 < len(registry_ids) and registry[registry_ids[block_end + 1]][1].strip().startswith("|"):
            block_end += 1
        extra_indices = sorted(order[line_id] for line_id in ordered if line_id not in {header[0], row[0]})
        before = [index for index in extra_indices if index < block_start]
        after = [index for index in extra_indices if index > block_end]
        inside = [index for index in extra_indices if block_start <= index <= block_end]
        selected_extra = set(extra_indices)
        before_separated = before and any(
            index not in selected_extra and not _is_generic_layout_line(registry[registry_ids[index]][1])
            for index in range(before[0], block_start)
        )
        after_separated = after and any(
            index not in selected_extra and not _is_generic_layout_line(registry[registry_ids[index]][1])
            for index in range(block_end + 1, after[-1] + 1)
        )
        if inside or before_separated or after_separated:
            raise ValueError(f"{provider} fact {ordinal} table context is not adjacent to its table")
    elif header or row:
        raise ValueError(f"{provider} fact {ordinal} non-table scope has table-only line IDs")
    return ordered


def _validate_field_grounding(
    provider: str,
    ordinal: int,
    raw_fact: dict[str, Any],
    fact: dict[str, Any],
    registry: dict[str, tuple[int, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    scope_ids = _scope_line_ids(provider, ordinal, raw_fact.get("relation_scope"), registry)
    scope = raw_fact["relation_scope"]
    table_roles: list[str | None] = []
    table_row_cells: list[tuple[int, int, str]] = []
    shared_heading: str | None = None
    if scope["scope_type"] == "table_row":
        header_id, row_id = scope["header_line_ids"][0], scope["row_line_ids"][0]
        header_cells = _table_cells(registry[header_id][1])
        table_row_cells = _table_cells(registry[row_id][1])
        if len(header_cells) != len(table_row_cells):
            raise ValueError(f"{provider} fact {ordinal} table header and row column counts differ")
        table_roles = [_table_header_role(cell[2]) for cell in header_cells]
        shared_heading = _shared_table_heading(scope, registry)
    field_evidence = raw_fact.get("field_evidence")
    if not isinstance(field_evidence, dict):
        raise ValueError(f"{provider} fact {ordinal} field_evidence is required")
    evidence: dict[str, Any] = {}
    if set(field_evidence) != set(RELATION_FIELDS):
        raise ValueError(f"{provider} fact {ordinal} field_evidence keys are incomplete")
    field_spans: dict[str, list[tuple[str, int, int]]] = {}
    source_fields = {field: "" for field in RELATION_FIELDS}
    line_order = {line_id: index for index, line_id in enumerate(registry)}
    source_ids = list(registry)
    for field in RELATION_FIELDS:
        value = fact[field]
        supplied = field_evidence.get(field)
        if not isinstance(supplied, list):
            raise ValueError(f"{provider} fact {ordinal} {field} field evidence must be an array")
        if not value:
            if supplied:
                raise ValueError(f"{provider} fact {ordinal} empty {field} has field evidence")
            continue
        if not supplied:
            raise ValueError(f"{provider} fact {ordinal} {field} field evidence is required")
        line_ids: list[str] = []
        fragments: list[str] = []
        locations: list[tuple[str, int, int]] = []
        for fragment in supplied:
            if not isinstance(fragment, dict):
                raise ValueError(f"{provider} fact {ordinal} {field} fragment is invalid")
            line_id, source_fragment = fragment.get("line_id"), fragment.get("fragment")
            start, end = fragment.get("char_start"), fragment.get("char_end")
            if not isinstance(line_id, str) or line_id not in registry or not isinstance(source_fragment, str) or isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
                raise ValueError(f"{provider} fact {ordinal} {field} fragment location is invalid")
            source_line = registry[line_id][1]
            allowed_ranges: list[tuple[int, int]] | None = None
            if scope["scope_type"] == "table_row" and line_id == scope["row_line_ids"][0] and field in TABLE_FIELD_ROLE:
                role = TABLE_FIELD_ROLE[field]
                allowed_ranges = [(cell[0], cell[1]) for index, cell in enumerate(table_row_cells) if table_roles[index] == role]
                if not allowed_ranges:
                    allowed_ranges = None if field in {"condition", "cap", "frequency", "period", "exceptions"} else []
            if allowed_ranges == []:
                raise ValueError(f"{provider} fact {ordinal} table {field} column is ambiguous or missing")
            try:
                start, end = _same_line_fragment_range(source_line, source_fragment, start, end, allowed_ranges)
            except ValueError as error:
                raise ValueError(f"{provider} fact {ordinal} {field} {error}") from error
            source_fragment = source_line[start:end]
            line_ids.append(line_id)
            fragments.append(source_fragment)
            locations.append((line_id, start, end))
        if not set(line_ids) <= set(scope_ids):
            raise ValueError(f"{provider} fact {ordinal} {field} evidence escapes relation_scope")
        ordered_locations = sorted(locations, key=lambda item: (line_order[item[0]], item[1], item[2]))
        if locations != ordered_locations or any(
            left[0] == right[0] and left[2] > right[1]
            for left, right in zip(ordered_locations, ordered_locations[1:])
        ):
            raise ValueError(f"{provider} fact {ordinal} {field} fragments must be ordered non-overlapping source ranges")
        for left, right in zip(ordered_locations, ordered_locations[1:]):
            if left[0] == right[0]:
                gap = registry[left[0]][1][left[2]:right[1]]
            else:
                between = source_ids[line_order[left[0]] + 1:line_order[right[0]]]
                gap = "\n".join([registry[left[0]][1][left[2]:],
                                 *(registry[line_id][1] for line_id in between),
                                 registry[right[0]][1][:right[1]]])
            # Only line-initial Markdown heading markers are layout. Do not
            # erase pipes, minus signs, comparison operators or prose in gaps.
            layout_gap = gap
            if left[0] != right[0]:
                # Only actual starts of subsequent source lines may contain
                # bullets; a '-' following a fragment can be a range/operator.
                tail, *following = gap.split("\n")
                layout_gap = "\n".join([tail, *(re.sub(r"^ {0,3}(?:#{1,6}[ \t]+|(?:[-*•·][ \t]+)+)", "", line) for line in following)])
            if not re.fullmatch(r"[\s•※]*", layout_gap):
                raise ValueError(f"{provider} fact {ordinal} {field} fragments skip non-whitespace source content")
        field_spans[field] = ordered_locations
        # Do not let a correct small fragment plus an unrelated broad quote pass.
        # The ordered provider fragments must reconstruct the complete raw field.
        source_value = " ".join(fragments)
        source_fields[field] = source_value
        if field not in {"value", "unit"} and field_comparison_key(source_fields, field) != field_comparison_key(fact, field):
            error_type = CriticalContentMismatch if (
                typed_literals(source_value) != typed_literals(value)
                or condition_operators(source_value) != condition_operators(value)
            ) else ValueError
            raise error_type(f"{provider} fact {ordinal} {field} fragments do not reconstruct its raw field")
        resolved = _resolve_evidence(
            provider,
            f"fact {ordinal} {field}",
            {"line_ids": list(dict.fromkeys(line_ids))},
            {},
            registry,
        )
        evidence[field] = {**resolved, "fragments": [
            {**fragment, "fragment": registry[location[0]][1][location[1]:location[2]], "char_start": location[1], "char_end": location[2]}
            for fragment, location in zip(supplied, locations)
        ]}
    # Compare value+unit together: 30 + 만원 and 300000 + 원 are the same
    # amount. Source coordinates/units below still refer to the original OCR.
    source_fact = normalise_fact(source_fields)
    for field in ("value", "unit"):
        if field_comparison_key(source_fact, field) != field_comparison_key(fact, field):
            raise CriticalContentMismatch(f"{provider} fact {ordinal} {field} fragments do not reconstruct its raw field")
    all_spans = [span for spans in field_spans.values() for span in spans]
    if scope["scope_type"] == "table_row":
        structural_ids = {scope["header_line_ids"][0], scope["row_line_ids"][0]}
        for line_id in set(scope_ids) - structural_ids:
            if not any(span[0] == line_id for span in all_spans):
                raise ValueError(f"{provider} fact {ordinal} table context line has no field-level relationship evidence")
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
            table_common_title = (
                scope["scope_type"] == "table_row"
                and line_id not in {scope["header_line_ids"][0], scope["row_line_ids"][0]}
                and not NUMBER_TOKEN.search(source_line)
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
    if scope["scope_type"] == "table_row":
        header_id, row_id = scope["header_line_ids"][0], scope["row_line_ids"][0]
        for column, (_start, _end, text) in enumerate(table_row_cells):
            if NUMBER_TOKEN.search(text) and table_roles[column] is None:
                raise ValueError(f"{provider} fact {ordinal} table numeric column has an unknown role")
        for field, expected_role in TABLE_FIELD_ROLE.items():
            if not fact[field]:
                continue
            matching_columns = [index for index, role in enumerate(table_roles) if role == expected_role]
            spans = field_spans[field]
            if matching_columns:
                if len(matching_columns) != 1:
                    raise ValueError(f"{provider} fact {ordinal} table {field} column is ambiguous or missing")
                cell_start, cell_end, _text = table_row_cells[matching_columns[0]]
                if not all(span[0] == row_id and cell_start <= span[1] and span[2] <= cell_end for span in spans):
                    raise ValueError(f"{provider} fact {ordinal} table {field} is not grounded in its matching data cell")
            elif field in {"target", "value", "unit"}:
                if not shared_heading or not all(span[0] == shared_heading for span in spans):
                    raise ValueError(f"{provider} fact {ordinal} table {field} column is ambiguous or missing")
            elif any(span[0] in {header_id, row_id} for span in spans):
                raise ValueError(f"{provider} fact {ordinal} table {field} column is ambiguous or missing")
        for column, role in enumerate(table_roles):
            if role is None:
                continue
            cell_start, cell_end, _text = table_row_cells[column]
            fields = ("value", "unit") if role == "value" else (role,)
            spans = [span for field in fields for span in field_spans.get(field, [])]
            if any(not (registry[row_id][1][position].isspace() or registry[row_id][1][position] == "•") and not any(
                _contains_range(span, row_id, position, position + 1) for span in spans
            ) for position in range(cell_start, cell_end)):
                raise ValueError(f"{provider} fact {ordinal} table {role} cell is only partially preserved")
        inherited = any(fact[field] and TABLE_FIELD_ROLE[field] not in table_roles for field in ("target", "value", "unit"))
        if inherited:
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
        for line_id in scope_ids:
            source_line = registry[line_id][1]
            for field, marker in FIELD_ROLE_MARKERS.items():
                for match in marker.finditer(source_line):
                    permitted = ("exceptions", "condition", "action") if field == "exceptions" else (field,)
                    if (field == "condition" and re.sub(r"\s+", "", match.group()) == "이용금액"
                            and not NUMBER_TOKEN.search(source_line)
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
        for match in NUMBER_TOKEN.finditer(registry[line_id][1]):
            owners = [
                field for field, spans in field_spans.items()
                if field != "unit" and any(_contains_range(span, line_id, match.start(), match.end()) for span in spans)
            ]
            if len(owners) != 1:
                raise ValueError(f"{provider} fact {ordinal} source numeric range is missing or ambiguously mapped")
            numeric_owner[(line_id, match.start(), match.end())] = owners[0]
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
                    and span[1] + start <= match.start() and match.end() <= span[1] + end
                    for span in field_spans[owners[0]]
                    for start, end in _context_literal_ranges(registry[span[0]][1][span[1]:span[2]], owners[0])
                )
                if not table_role_bound and not marker_bound and not context_literal_bound:
                    raise ValueError(f"{provider} fact {ordinal} numeric relationship is assigned to the wrong field role")
            suffix = SOURCE_UNIT.match(registry[line_id][1], match.end())
            if suffix:
                if owners[0] in {"benefit_type", "action", "target"}:
                    raise ValueError(f"{provider} fact {ordinal} benefit numeric range is assigned to a non-numeric field")
                unit_start = match.end()
                while registry[line_id][1][unit_start].isspace():
                    unit_start += 1
                allowed = [*field_spans[owners[0]], *(field_spans.get("unit", []) if owners[0] == "value" else [])]
                if not any(_contains_range(span, line_id, unit_start, suffix.end()) for span in allowed):
                    raise ValueError(f"{provider} fact {ordinal} source numeric unit is missing from its owning field")
    if source_fact["unit"]:
        attached = False
        for (line_id, start, end), owner in numeric_owner.items():
            if owner != "value":
                continue
            source_line = registry[line_id][1]
            unit_start = end
            while unit_start < len(source_line) and source_line[unit_start].isspace():
                unit_start += 1
            unit_end = unit_start + len(source_fact["unit"])
            if normalized(source_line[unit_start:unit_end]) == source_fact["unit"] and any(_contains_range(span, line_id, unit_start, unit_end) for span in field_spans.get("unit", [])):
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
            fact = normalise_fact(raw_fact)
            relation_scope, field_evidence = _validate_field_grounding(provider, ordinal, raw_fact, fact, registry)
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
            if line_id not in referenced and CRITICAL_RELATION_LINE.search(line)
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


def validation_diagnostics(payloads: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Run four read-only validation jobs independently; callers persist on main thread."""
    tasks: dict[str, Callable[[], Any]] = {
        "ocr_comparison": lambda: compare_ocr_outputs(payloads["luna"], payloads["upstage"]),
        "luna_text_to_json": lambda: validate_lane("luna", payloads["luna"]),
        "upstage_text_to_json": lambda: validate_lane("upstage", payloads["upstage"]),
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


def diagnostic_json_comparison(luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> dict[str, Any]:
    def relations(payload: dict[str, Any]) -> tuple[Counter[tuple[str, ...]], list[str]]:
        keys: list[tuple[str, ...]] = []
        errors: list[str] = []
        for ordinal, fact in enumerate(payload.get("facts", [])):
            if isinstance(fact, dict):
                try:
                    keys.append(relation_tuple(normalise_fact(fact)))
                except ValueError as error:
                    errors.append(f"fact {ordinal}: {error}")
            else:
                errors.append(f"fact {ordinal}: not an object")
        return Counter(keys), errors

    luna_relations, luna_errors = relations(luna_payload)
    upstage_relations, upstage_errors = relations(upstage_payload)
    return {
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
