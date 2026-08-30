from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import random
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import pymupdf

from common import (
    RUNTIME_DIR,
    SourceDocument,
    discover_documents,
    exclusive_run_lock,
    load_env_key,
    read_json,
    value_sha256,
    write_json,
)
from run_luna_parse import complete_artifact as complete_luna_artifact
from run_luna_parse import config as luna_config


UPSTAGE_URL = "https://api.upstage.ai/v1/document-digitization"
MODEL = "document-parse"
COST_PER_PAGE_USD = 0.01
NORMALIZER_VERSION = "full-corpus-v4"
LEGACY_NORMALIZER_VERSION = "full-corpus-v3"
CONTENTLESS_DETECTOR_VERSION = "source-page-contentless-v1"
CONTENTLESS_RENDER_DPI = 100
DOMINANT_RGB_RATIO_THRESHOLD = 0.995
MAX_CONTENTLESS_DRAWINGS = 3
OUTPUT_DIR = RUNTIME_DIR / "upstage"
RAW_OUTPUT_DIR = RUNTIME_DIR / "upstage_raw"
PRIMARY_DIR = RUNTIME_DIR / "luna_200dpi"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def config(normalizer_version: str = NORMALIZER_VERSION) -> dict[str, Any]:
    value: dict[str, Any] = {
        "provider": "upstage",
        "model": MODEL,
        "ocr": "force",
        "coordinates": True,
        "output_formats": ["html", "markdown"],
        "normalizer_version": normalizer_version,
    }
    if normalizer_version == NORMALIZER_VERSION:
        value["contentless_detector"] = {
            "version": CONTENTLESS_DETECTOR_VERSION,
            "render_dpi": CONTENTLESS_RENDER_DPI,
            "dominant_rgb_ratio_threshold": DOMINANT_RGB_RATIO_THRESHOLD,
            "max_drawings_with_empty_native_text_and_no_images": MAX_CONTENTLESS_DRAWINGS,
        }
    return value


class UpstageHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: str, retry_after_seconds: float | None = None):
        super().__init__(f"Upstage HTTP {status_code}: {body[-2000:]}")
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def is_retryable_error(error: Exception) -> bool:
    if isinstance(error, UpstageHTTPError):
        return error.status_code in {408, 429} or 500 <= error.status_code <= 599
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException))


def retry_delay_seconds(error: Exception, attempt: int) -> float:
    fallback = min(30.0, 2**attempt + random.random())
    if isinstance(error, UpstageHTTPError) and error.retry_after_seconds is not None:
        return max(fallback, error.retry_after_seconds)
    return fallback


def validate_resolved_models(values: Any) -> str | None:
    models = sorted({value.strip() for value in values if isinstance(value, str) and value.strip()})
    if len(models) > 1:
        raise ValueError(f"mixed Upstage resolved models: {models}")
    return models[0] if models else None


def output_path(document: SourceDocument) -> Path:
    return OUTPUT_DIR / document.issuer / f"{document.card_name}.json"


def raw_output_path(document: SourceDocument) -> Path:
    return RAW_OUTPUT_DIR / document.issuer / f"{document.card_name}.json"


def multipart_body(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----pickcardu-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="document"; filename="{file_path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


def request_parse(pdf_path: Path, api_key: str, timeout: int) -> dict[str, Any]:
    body, boundary = multipart_body(
        {
            "model": MODEL,
            "ocr": "force",
            "coordinates": "true",
            "output_formats": '["html", "markdown"]',
        },
        pdf_path,
    )
    request = urllib.request.Request(
        UPSTAGE_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        retry_after = error.headers.get("Retry-After") if error.headers else None
        try:
            retry_after_seconds = float(retry_after) if retry_after is not None else None
        except ValueError:
            retry_after_seconds = None
        raise UpstageHTTPError(error.code, body_text, retry_after_seconds) from error


def bbox_from_coordinates(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict) and all(key in value for key in ("x", "y")):
        coordinate = {"x1": float(value["x"]), "y1": float(value["y"]), "x2": float(value["x"]), "y2": float(value["y"])}
        return coordinate
    if isinstance(value, dict):
        for key in ("points", "vertices", "coordinates"):
            if key in value:
                return bbox_from_coordinates(value[key])
    if isinstance(value, list) and value:
        points = [point for point in value if isinstance(point, dict) and "x" in point and "y" in point]
        if points:
            return {
                "x1": min(float(point["x"]) for point in points),
                "y1": min(float(point["y"]) for point in points),
                "x2": max(float(point["x"]) for point in points),
                "y2": max(float(point["y"]) for point in points),
            }
    return None


def element_text(element: dict[str, Any]) -> str:
    content = element.get("content")
    if isinstance(content, dict):
        return str(content.get("markdown") or content.get("html") or content.get("text") or "").strip()
    return str(content or element.get("markdown") or element.get("html") or element.get("text") or "").strip()


def provider_elements(provider_response: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(provider_response.get("elements"), list):
        return provider_response["elements"]
    content = provider_response.get("content")
    if isinstance(content, dict) and isinstance(content.get("elements"), list):
        return content["elements"]
    return []


def coordinate_space(blocks: list[dict[str, Any]]) -> str:
    coordinates = [value for block in blocks if (value := block.get("bbox")) is not None]
    if coordinates and all(0.0 <= value[key] <= 1.01 for value in coordinates for key in ("x1", "y1", "x2", "y2")):
        return "normalized_0_1"
    return "provider_coordinates"


def meaningful_layout_block(block: Any) -> bool:
    return isinstance(block, dict) and (
        bool(str(block.get("text", "")).strip()) or block.get("bbox") is not None
    )


def source_page_contentless_evidence(document: SourceDocument, page_num: int) -> dict[str, Any]:
    if not 1 <= page_num <= document.page_count:
        raise ValueError(f"source page {page_num} is outside {document.document_id}")
    with pymupdf.open(document.path) as source:
        if source.page_count != document.page_count:
            raise ValueError(
                f"PyMuPDF reported {source.page_count} pages for {document.document_id}; "
                f"expected {document.page_count}"
            )
        page = source[page_num - 1]
        native_text = page.get_text("text").strip()
        image_count = len(page.get_images(full=True))
        drawing_count = len(page.get_drawings())
        pixmap = page.get_pixmap(
            dpi=CONTENTLESS_RENDER_DPI,
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
    samples = pixmap.samples
    pixels = pixmap.width * pixmap.height
    colors = Counter(zip(samples[0::3], samples[1::3], samples[2::3]))
    dominant_rgb, dominant_count = colors.most_common(1)[0]
    dominant_rgb_ratio = dominant_count / pixels if pixels else 0.0
    dominant_match = dominant_rgb_ratio >= DOMINANT_RGB_RATIO_THRESHOLD
    native_structure_match = (
        not native_text
        and image_count == 0
        and drawing_count <= MAX_CONTENTLESS_DRAWINGS
    )
    method = (
        "dominant_rendered_rgb"
        if dominant_match
        else "native_empty_no_images_low_drawings"
        if native_structure_match
        else "not_contentless"
    )
    return {
        "method": method,
        "is_contentless": dominant_match or native_structure_match,
        "detector_version": CONTENTLESS_DETECTOR_VERSION,
        "render_dpi": CONTENTLESS_RENDER_DPI,
        "dominant_rgb": list(dominant_rgb),
        "dominant_rgb_ratio": round(dominant_rgb_ratio, 6),
        "dominant_rgb_ratio_threshold": DOMINANT_RGB_RATIO_THRESHOLD,
        "native_text": native_text,
        "native_text_empty": not native_text,
        "image_count": image_count,
        "drawing_count": drawing_count,
        "max_contentless_drawings": MAX_CONTENTLESS_DRAWINGS,
        "renderer": "pymupdf",
        "renderer_version": pymupdf.VersionBind,
    }


def normalize_pages(
    provider_response: dict[str, Any],
    page_count: int,
    blank_page_numbers: set[int] | None = None,
) -> list[dict[str, Any]]:
    blank_page_numbers = blank_page_numbers or set()
    pages = {
        page_num: {
            "page_num": page_num,
            "status": "success",
            "is_blank": page_num in blank_page_numbers,
            "blocks": [],
            "tables": [],
        }
        for page_num in range(1, page_count + 1)
    }
    for reading_order, element in enumerate(provider_elements(provider_response), start=1):
        raw_page_num = element.get("page", element.get("page_number", 1))
        try:
            page_num = int(raw_page_num)
        except (TypeError, ValueError):
            continue
        if page_num not in pages:
            continue
        page = pages[page_num]
        block_type = str(element.get("category") or element.get("type") or "unknown").lower()
        text = element_text(element)
        bbox = bbox_from_coordinates(element.get("coordinates") or element.get("bounding_box") or element.get("bbox"))
        source_id = element.get("id")
        block_id = f"p{page_num}_b{len(page['blocks']) + 1:04d}"
        block: dict[str, Any] = {
            "block_id": block_id,
            "reading_order": reading_order,
            "type": block_type,
            "text": text,
            "bbox": bbox,
            "source_id": source_id,
        }
        if block_type == "table":
            table_id = str(source_id if source_id is not None else f"p{page_num}_t{len(page['tables']) + 1:03d}")
            block["table_id"] = table_id
            page["tables"].append(
                {
                    "table_id": table_id,
                    "format": "markdown" if "|" in text else "html",
                    "content": text,
                    "bbox": bbox,
                    "source_id": source_id,
                }
            )
        page["blocks"].append(block)

    result = []
    for page in pages.values():
        page["coordinate_space"] = coordinate_space(page["blocks"])
        result.append(page)
    return result


def valid_layout_pages(pages: Any, expected_count: int) -> bool:
    if not isinstance(pages, list) or len(pages) != expected_count:
        return False
    if not all(isinstance(page, dict) for page in pages):
        return False
    if [page.get("page_num") for page in pages] != list(range(1, expected_count + 1)):
        return False
    return all(
        page.get("status") == "success"
        and isinstance(page.get("blocks"), list)
        and isinstance(page.get("tables"), list)
        and (
            page.get("is_blank") is True
            or any(meaningful_layout_block(block) for block in page.get("blocks", []))
        )
        for page in pages
    )


def load_current_luna_page_state(document: SourceDocument) -> tuple[set[int], set[int]]:
    primary_path = PRIMARY_DIR / document.issuer / f"{document.card_name}.json"
    if not primary_path.exists():
        raise ValueError(f"completed Luna artifact is required before Upstage: {document.document_id}")
    primary = read_json(primary_path)
    luna_batch_pages = int(primary.get("parser", {}).get("batch_pages", 0))
    luna_config_sha256 = value_sha256(luna_config(luna_batch_pages)) if luna_batch_pages > 0 else ""
    if not complete_luna_artifact(primary_path, document, luna_config_sha256):
        raise ValueError(f"current completed Luna artifact is required before Upstage: {document.document_id}")
    blank_page_numbers = {
        int(page["page_num"])
        for page in primary.get("pages", [])
        if page.get("is_blank") is True
    }
    empty_markdown_page_numbers = {
        int(page["page_num"])
        for page in primary.get("pages", [])
        if isinstance(page.get("markdown"), str) and not page["markdown"].strip()
    }
    return blank_page_numbers, empty_markdown_page_numbers


def load_current_luna_blank_pages(document: SourceDocument) -> set[int]:
    return load_current_luna_page_state(document)[0]


def validate_provider_response(
    raw: Any,
    document: SourceDocument,
    blank_page_numbers: set[int],
    empty_markdown_page_numbers: set[int] | None = None,
) -> dict[str, Any]:
    empty_markdown_page_numbers = empty_markdown_page_numbers or set()
    if not isinstance(raw, dict):
        raise ValueError("Upstage response must be a JSON object")
    usage = raw.get("usage")
    if not isinstance(usage, dict) or usage.get("pages") != document.page_count:
        reported = usage.get("pages") if isinstance(usage, dict) else None
        raise ValueError(f"Upstage reported {reported!r} pages; expected {document.page_count}")

    elements = provider_elements(raw)
    covered_pages: set[int] = set()
    for element in elements:
        if not isinstance(element, dict):
            raise ValueError("Upstage elements must be JSON objects")
        raw_page_num = element.get("page", element.get("page_number", 1))
        try:
            page_num = int(raw_page_num)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Upstage element has an invalid page number: {raw_page_num!r}") from error
        if not 1 <= page_num <= document.page_count:
            raise ValueError(f"Upstage element page {page_num} is outside the source document")
        covered_pages.add(page_num)

    missing_pages = set(range(1, document.page_count + 1)) - covered_pages
    effective_blank_pages = set(blank_page_numbers)
    blank_page_evidence: dict[int, dict[str, Any]] = {}
    for page_num in sorted(missing_pages):
        evidence = None
        if page_num in empty_markdown_page_numbers:
            evidence = source_page_contentless_evidence(document, page_num)
        if evidence is not None and evidence["is_contentless"]:
            if page_num in blank_page_numbers:
                evidence["luna_is_blank"] = True
            effective_blank_pages.add(page_num)
            blank_page_evidence[page_num] = evidence
        elif page_num in blank_page_numbers:
            blank_page_evidence[page_num] = {
                "method": "luna_is_blank",
                "luna_is_blank": True,
                "source_page_evidence": evidence,
            }

    missing_nonblank_pages = sorted(missing_pages - effective_blank_pages)
    if missing_nonblank_pages:
        raise ValueError(f"Upstage response omits nonblank source pages: {missing_nonblank_pages}")

    pages = normalize_pages(raw, document.page_count, effective_blank_pages)
    for page in pages:
        if page["page_num"] in blank_page_evidence:
            page["blank_provenance"] = blank_page_evidence[page["page_num"]]
    if not valid_layout_pages(pages, document.page_count):
        invalid_nonblank_pages = [
            page["page_num"]
            for page in pages
            if page.get("is_blank") is not True
            and not any(meaningful_layout_block(block) for block in page.get("blocks", []))
        ]
        raise ValueError(f"Upstage nonblank pages lack meaningful layout blocks: {invalid_nonblank_pages}")

    resolved_model = raw.get("model")
    provider_api_version = raw.get("api")
    if not isinstance(resolved_model, str) or not resolved_model.strip():
        raise ValueError("Upstage response is missing the resolved model")
    if not isinstance(provider_api_version, str) or not provider_api_version.strip():
        raise ValueError("Upstage response is missing the API version")
    return {
        "pages": pages,
        "omitted_blank_pages": sorted(missing_pages),
        "blank_page_evidence": {
            str(page_num): evidence
            for page_num, evidence in sorted(blank_page_evidence.items())
        },
        "resolved_model": resolved_model,
        "provider_api_version": provider_api_version,
        "raw_response_sha256": value_sha256(raw),
    }


def recoverable_failed_artifact(
    failure: Any,
    raw: Any,
    document: SourceDocument,
    parser_config: dict[str, Any],
    config_sha256: str,
    blank_page_numbers: set[int],
    empty_markdown_page_numbers: set[int] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(failure, dict) or failure.get("run_status") != "failed":
        return None
    if failure.get("document_id") != document.document_id or failure.get("source") != document.as_dict():
        return None
    parser = failure.get("parser")
    if not isinstance(parser, dict) or parser.get("config_sha256") != config_sha256:
        return None
    if any(parser.get(key) != value for key, value in parser_config.items()):
        return None
    if not isinstance(raw, dict):
        return None
    if (
        parser.get("raw_response_sha256") != value_sha256(raw)
        or parser.get("resolved_model") != raw.get("model")
        or parser.get("provider_api_version") != raw.get("api")
    ):
        return None
    try:
        validated = validate_provider_response(
            raw,
            document,
            blank_page_numbers,
            empty_markdown_page_numbers,
        )
        attempt_count = int(failure.get("attempt_count", 0))
    except (AttributeError, TypeError, ValueError):
        return None
    if attempt_count < 1 or failure.get("estimated_cost_basis") != "submitted_attempts":
        return None
    cumulative_estimated_cost_usd = round(attempt_count * document.page_count * COST_PER_PAGE_USD, 4)
    if failure.get("cumulative_estimated_cost_usd") != cumulative_estimated_cost_usd:
        return None
    return {
        "schema_version": "2.0",
        "document_id": document.document_id,
        "source": document.as_dict(),
        "parser": {
            **parser_config,
            "resolved_model": validated["resolved_model"],
            "provider_api_version": validated["provider_api_version"],
            "raw_response_sha256": validated["raw_response_sha256"],
            "config_sha256": config_sha256,
        },
        "run_status": "completed",
        "started_at": failure.get("started_at"),
        "finished_at": now(),
        "elapsed_seconds": failure.get("elapsed_seconds"),
        "attempt_count": attempt_count,
        "current_run_attempt_count": 0,
        "estimated_cost_usd": cumulative_estimated_cost_usd,
        "cumulative_estimated_cost_usd": cumulative_estimated_cost_usd,
        "estimated_cost_basis": "submitted_attempts",
        "omitted_blank_pages": validated["omitted_blank_pages"],
        "blank_page_evidence": validated["blank_page_evidence"],
        "recovery": {
            "method": "validated_existing_raw",
            "external_request_performed": False,
            "previous_error": failure.get("error"),
        },
        "pages": validated["pages"],
    }


def validated_attempt_accounting(artifact: dict[str, Any], document: SourceDocument) -> tuple[int, float] | None:
    attempt_count = artifact.get("attempt_count")
    current_run_attempt_count = artifact.get("current_run_attempt_count")
    if (
        type(attempt_count) is not int
        or attempt_count < 1
        or type(current_run_attempt_count) is not int
        or not 0 <= current_run_attempt_count <= attempt_count
        or artifact.get("estimated_cost_basis") != "submitted_attempts"
    ):
        return None
    cumulative_estimated_cost_usd = round(attempt_count * document.page_count * COST_PER_PAGE_USD, 4)
    if (
        artifact.get("estimated_cost_usd") != cumulative_estimated_cost_usd
        or artifact.get("cumulative_estimated_cost_usd") != cumulative_estimated_cost_usd
    ):
        return None
    return attempt_count, cumulative_estimated_cost_usd


def migrate_v3_artifact(
    legacy_artifact: Any,
    raw: Any,
    document: SourceDocument,
    blank_page_numbers: set[int],
    empty_markdown_page_numbers: set[int],
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(legacy_artifact, dict) or legacy_artifact.get("schema_version") != "2.0":
        return None, "legacy artifact is not schema 2.0 JSON"
    source_run_status = legacy_artifact.get("run_status")
    if source_run_status not in {"completed", "failed"}:
        return None, f"legacy run_status is not migratable: {source_run_status!r}"
    if (
        legacy_artifact.get("document_id") != document.document_id
        or legacy_artifact.get("source") != document.as_dict()
    ):
        return None, "legacy document identity or source metadata does not match"

    legacy_parser_config = config(LEGACY_NORMALIZER_VERSION)
    legacy_config_sha256 = value_sha256(legacy_parser_config)
    parser = legacy_artifact.get("parser")
    if not isinstance(parser, dict):
        return None, "legacy parser metadata is missing"
    if (
        parser.get("config_sha256") != legacy_config_sha256
        or any(parser.get(key) != value for key, value in legacy_parser_config.items())
    ):
        return None, "legacy parser config does not match full-corpus-v3"
    if not isinstance(raw, dict):
        return None, "legacy raw response is not a JSON object"
    if (
        parser.get("raw_response_sha256") != value_sha256(raw)
        or parser.get("resolved_model") != raw.get("model")
        or parser.get("provider_api_version") != raw.get("api")
    ):
        return None, "legacy model/API/raw provenance does not match"
    accounting = validated_attempt_accounting(legacy_artifact, document)
    if accounting is None:
        return None, "legacy attempt or cost accounting does not match"
    try:
        validated = validate_provider_response(
            raw,
            document,
            blank_page_numbers,
            empty_markdown_page_numbers,
        )
    except (AttributeError, TypeError, ValueError) as error:
        return None, f"legacy raw response is not valid under v4: {error}"

    attempt_count, cumulative_estimated_cost_usd = accounting
    parser_config = config()
    config_sha256 = value_sha256(parser_config)
    migrated = {
        "schema_version": "2.0",
        "document_id": document.document_id,
        "source": document.as_dict(),
        "parser": {
            **parser_config,
            "resolved_model": validated["resolved_model"],
            "provider_api_version": validated["provider_api_version"],
            "raw_response_sha256": validated["raw_response_sha256"],
            "config_sha256": config_sha256,
        },
        "run_status": "completed",
        "started_at": legacy_artifact.get("started_at"),
        "finished_at": now(),
        "elapsed_seconds": legacy_artifact.get("elapsed_seconds"),
        "attempt_count": attempt_count,
        "current_run_attempt_count": 0,
        "estimated_cost_usd": cumulative_estimated_cost_usd,
        "cumulative_estimated_cost_usd": cumulative_estimated_cost_usd,
        "estimated_cost_basis": "submitted_attempts",
        "omitted_blank_pages": validated["omitted_blank_pages"],
        "blank_page_evidence": validated["blank_page_evidence"],
        "migration": {
            "from_normalizer_version": LEGACY_NORMALIZER_VERSION,
            "from_config_sha256": legacy_config_sha256,
            "source_run_status": source_run_status,
            "external_request_performed": False,
            "previous_error": legacy_artifact.get("error"),
        },
        "pages": validated["pages"],
    }
    return migrated, "migrated full-corpus-v3 artifact and raw response locally"


def complete_artifact(path: Path, document: SourceDocument, config_sha256: str) -> bool:
    if not path.exists():
        return False
    try:
        artifact = read_json(path)
        raw = read_json(raw_output_path(document))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(artifact, dict) or not isinstance(raw, dict):
        return False
    parser = artifact.get("parser", {})
    if not isinstance(parser, dict):
        return False
    try:
        blank_page_numbers, empty_markdown_page_numbers = load_current_luna_page_state(document)
        validated = validate_provider_response(
            raw,
            document,
            blank_page_numbers,
            empty_markdown_page_numbers,
        )
    except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return False
    parser_config = config()
    return (
        artifact.get("run_status") == "completed"
        and artifact.get("document_id") == document.document_id
        and artifact.get("source") == document.as_dict()
        and parser.get("config_sha256") == config_sha256
        and all(parser.get(key) == value for key, value in parser_config.items())
        and isinstance(parser.get("resolved_model"), str)
        and bool(parser["resolved_model"].strip())
        and isinstance(parser.get("provider_api_version"), str)
        and bool(parser["provider_api_version"].strip())
        and isinstance(parser.get("raw_response_sha256"), str)
        and parser["raw_response_sha256"] == value_sha256(raw)
        and parser["resolved_model"] == raw.get("model")
        and parser["provider_api_version"] == raw.get("api")
        and artifact.get("pages") == validated["pages"]
    )


def process_document(
    document: SourceDocument,
    api_key: str,
    timeout: int,
    max_attempts: int,
    force: bool,
    offline_recover_only: bool = False,
) -> dict[str, Any]:
    parser_config = config()
    config_sha256 = value_sha256(parser_config)
    destination = output_path(document)
    if not force and complete_artifact(destination, document, config_sha256):
        existing = read_json(destination)
        return {
            "status": "skipped",
            "document_id": document.document_id,
            "pages": document.page_count,
            "resolved_model": existing["parser"]["resolved_model"],
        }

    prior_attempt_count = 0
    if destination.exists():
        try:
            existing = read_json(destination)
            if (
                isinstance(existing, dict)
                and existing.get("source", {}).get("sha256") == document.sha256
                and existing.get("parser", {}).get("config_sha256") == config_sha256
            ):
                prior_attempt_count = max(0, int(existing.get("attempt_count", 0)))
        except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
            prior_attempt_count = 0

    blank_page_numbers, empty_markdown_page_numbers = load_current_luna_page_state(document)

    recovery_reason = "no matching local artifact and raw response"
    if not force and destination.exists() and raw_output_path(document).exists():
        try:
            local_artifact = read_json(destination)
            raw = read_json(raw_output_path(document))
        except (OSError, json.JSONDecodeError):
            local_artifact = None
            raw = None
            migration = None
            migration_reason = "local artifact or raw response is unreadable"
            recovered = None
        else:
            migration, migration_reason = migrate_v3_artifact(
                local_artifact,
                raw,
                document,
                blank_page_numbers,
                empty_markdown_page_numbers,
            )
            if migration is not None:
                write_json(destination, migration)
                return {
                    "status": "completed",
                    "document_id": document.document_id,
                    "pages": document.page_count,
                    "attempt_count": migration["attempt_count"],
                    "current_run_attempt_count": 0,
                    "cumulative_estimated_cost_usd": migration["cumulative_estimated_cost_usd"],
                    "resolved_model": migration["parser"]["resolved_model"],
                    "migrated_from_v3": True,
                }
            recovered = recoverable_failed_artifact(
                local_artifact,
                raw,
                document,
                parser_config,
                config_sha256,
                blank_page_numbers,
                empty_markdown_page_numbers,
            )
        if recovered is not None:
            write_json(destination, recovered)
            return {
                "status": "completed",
                "document_id": document.document_id,
                "pages": document.page_count,
                "attempt_count": recovered["attempt_count"],
                "current_run_attempt_count": 0,
                "cumulative_estimated_cost_usd": recovered["cumulative_estimated_cost_usd"],
                "resolved_model": recovered["parser"]["resolved_model"],
                "recovered_from_existing_raw": True,
            }
        recovery_reason = migration_reason

    if offline_recover_only:
        return {
            "status": "offline_recovery_failed",
            "document_id": document.document_id,
            "error": recovery_reason,
            "artifact_preserved": True,
            "external_request_performed": False,
        }

    started_at = now()
    started = time.perf_counter()
    last_error = ""
    current_run_attempt_count = 0
    last_response_provenance: dict[str, str] = {}
    for attempt in range(1, max_attempts + 1):
        current_run_attempt_count = attempt
        try:
            raw = request_parse(document.path, api_key, timeout)
            write_json(raw_output_path(document), raw)
            if not isinstance(raw, dict):
                raise ValueError("Upstage response must be a JSON object")
            last_response_provenance = {"raw_response_sha256": value_sha256(raw)}
            if isinstance(raw.get("model"), str):
                last_response_provenance["resolved_model"] = raw["model"]
            if isinstance(raw.get("api"), str):
                last_response_provenance["provider_api_version"] = raw["api"]
            validated = validate_provider_response(
                raw,
                document,
                blank_page_numbers,
                empty_markdown_page_numbers,
            )
            resolved_model = validated["resolved_model"]
            provider_api_version = validated["provider_api_version"]
            attempt_count = prior_attempt_count + current_run_attempt_count
            cumulative_estimated_cost_usd = round(attempt_count * document.page_count * COST_PER_PAGE_USD, 4)
            artifact = {
                "schema_version": "2.0",
                "document_id": document.document_id,
                "source": document.as_dict(),
                "parser": {
                    **parser_config,
                    "resolved_model": resolved_model,
                    "provider_api_version": provider_api_version,
                    "raw_response_sha256": validated["raw_response_sha256"],
                    "config_sha256": config_sha256,
                },
                "run_status": "completed",
                "started_at": started_at,
                "finished_at": now(),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "attempt_count": attempt_count,
                "current_run_attempt_count": current_run_attempt_count,
                "estimated_cost_usd": cumulative_estimated_cost_usd,
                "cumulative_estimated_cost_usd": cumulative_estimated_cost_usd,
                "estimated_cost_basis": "submitted_attempts",
                "omitted_blank_pages": validated["omitted_blank_pages"],
                "blank_page_evidence": validated["blank_page_evidence"],
                "pages": validated["pages"],
            }
            write_json(destination, artifact)
            return {
                "status": "completed",
                "document_id": document.document_id,
                "pages": document.page_count,
                "attempt_count": attempt_count,
                "current_run_attempt_count": current_run_attempt_count,
                "cumulative_estimated_cost_usd": cumulative_estimated_cost_usd,
                "resolved_model": resolved_model,
            }
        except Exception as error:
            last_error = f"{type(error).__name__}: {error}"
            if attempt >= max_attempts or not is_retryable_error(error):
                break
            time.sleep(retry_delay_seconds(error, attempt))

    attempt_count = prior_attempt_count + current_run_attempt_count
    cumulative_estimated_cost_usd = round(attempt_count * document.page_count * COST_PER_PAGE_USD, 4)

    failure = {
        "schema_version": "2.0",
        "document_id": document.document_id,
        "source": document.as_dict(),
        "parser": {
            **parser_config,
            **last_response_provenance,
            "config_sha256": config_sha256,
        },
        "run_status": "failed",
        "started_at": started_at,
        "finished_at": now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "attempt_count": attempt_count,
        "current_run_attempt_count": current_run_attempt_count,
        "estimated_cost_usd": cumulative_estimated_cost_usd,
        "cumulative_estimated_cost_usd": cumulative_estimated_cost_usd,
        "estimated_cost_basis": "submitted_attempts",
        "error": last_error,
    }
    write_json(destination, failure)
    return {
        "status": "failed",
        "document_id": document.document_id,
        "attempt_count": attempt_count,
        "current_run_attempt_count": current_run_attempt_count,
        "cumulative_estimated_cost_usd": cumulative_estimated_cost_usd,
        "error": last_error,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Upstage Document Parse as a layout validation pass.")
    parser.add_argument("--issuer", action="append", dest="issuers")
    parser.add_argument("--document", action="append", dest="documents")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--offline-recover-only",
        action="store_true",
        help="Only skip or locally migrate/recover artifacts; never call Upstage.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.workers < 1 or args.max_attempts < 1:
        raise SystemExit("--workers and --max-attempts must be positive")
    if args.force and args.offline_recover_only:
        raise SystemExit("--force cannot be combined with --offline-recover-only")
    documents = discover_documents(args.issuers, args.documents, args.limit)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "documents": len(documents),
                    "pages": sum(item.page_count for item in documents),
                    "estimated_cost_usd": round(sum(item.page_count for item in documents) * COST_PER_PAGE_USD, 2),
                    "config": config(),
                },
                ensure_ascii=False,
            )
        )
        return
    api_key = ""
    if not args.offline_recover_only:
        api_key = load_env_key("UPSTAGE_API_KEY") or ""
        if not api_key:
            raise SystemExit("UPSTAGE_API_KEY is required in the environment or .env")

    counts = {"completed": 0, "skipped": 0, "failed": 0, "offline_recovery_failed": 0}
    resolved_models: set[str] = set()
    with exclusive_run_lock("upstage-full-corpus"):
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    process_document,
                    document,
                    api_key,
                    args.timeout,
                    args.max_attempts,
                    args.force,
                    args.offline_recover_only,
                ): document
                for document in documents
            }
            try:
                for future in as_completed(futures):
                    document = futures[future]
                    try:
                        result = future.result()
                    except Exception as error:
                        status = "offline_recovery_failed" if args.offline_recover_only else "failed"
                        result = {
                            "status": status,
                            "document_id": document.document_id,
                            "error": f"{type(error).__name__}: {error}",
                            "artifact_preserved": args.offline_recover_only,
                            "external_request_performed": False if args.offline_recover_only else None,
                        }
                    counts[result["status"]] += 1
                    if result.get("resolved_model"):
                        resolved_models.add(result["resolved_model"])
                    print(json.dumps(result, ensure_ascii=False), flush=True)
            except KeyboardInterrupt:
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=True, cancel_futures=True)
                raise
    summary = {**counts, "resolved_models": sorted(resolved_models)}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    try:
        validate_resolved_models(resolved_models)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if counts["offline_recovery_failed"]:
        raise SystemExit(
            f"offline recovery failed for {counts['offline_recovery_failed']} document(s); "
            "no failed artifact was written and no external request was performed"
        )


if __name__ == "__main__":
    main()
