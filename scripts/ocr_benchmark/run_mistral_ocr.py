from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
import requests
from dotenv import load_dotenv

from run_pymupdf import RAW_DIR, ROOT, TARGETS


load_dotenv(ROOT / ".env")

BENCHMARK_DIR = ROOT / "data" / "ocr_benchmark"
LEGACY_OUTPUT_DIR = BENCHMARK_DIR / "mistral"
RAW_OUTPUT_DIR = BENCHMARK_DIR / "mistral_raw"
NORMALIZED_OUTPUT_DIR = BENCHMARK_DIR / "normalized" / "mistral"
TEXT_OUTPUT_DIR = BENCHMARK_DIR / "text" / "mistral"
MISTRAL_OCR_URL = "https://api.mistral.ai/v1/ocr"
MODEL = os.getenv("MISTRAL_MODEL_NAME", "mistral-ocr-4-0")
OCR_4_COST_PER_PAGE_USD = 0.004
TABLE_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^\)]+\)")


def bbox_from(value: Any) -> dict[str, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        x1, y1, x2, y2 = value
        return {"x1": float(x1), "y1": float(y1), "x2": float(x2), "y2": float(y2)}

    if isinstance(value, dict) and all(key in value for key in ("x0", "y0", "x1", "y1")):
        return {
            "x1": float(value["x0"]),
            "y1": float(value["y0"]),
            "x2": float(value["x1"]),
            "y2": float(value["y1"]),
        }

    if isinstance(value, dict) and all(
        key in value for key in ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")
    ):
        return {
            "x1": float(value["top_left_x"]),
            "y1": float(value["top_left_y"]),
            "x2": float(value["bottom_right_x"]),
            "y2": float(value["bottom_right_y"]),
        }

    return None


def has_duplicate_block(blocks: list[dict]) -> bool:
    texts = [block.get("text", "").strip() for block in blocks if block.get("text", "").strip()]
    return len(texts) != len(set(texts))


def table_bboxes(provider_page: dict) -> dict[str, dict[str, float] | None]:
    return {
        block["table_id"]: bbox_from(block)
        for block in provider_page.get("blocks") or []
        if block.get("type") == "table" and block.get("table_id")
    }


def normalize_tables(provider_page: dict, page_num: int) -> list[dict]:
    bboxes = table_bboxes(provider_page)
    tables = []

    for table_index, table in enumerate(provider_page.get("tables") or [], start=1):
        table_id = table.get("id") or f"p{page_num}_t{table_index:03d}"
        tables.append(
            {
                "table_id": table_id,
                "format": table.get("format") or "markdown",
                "content": table.get("content") or "",
                "bbox": bboxes.get(table_id),
            }
        )

    return tables


def build_search_text(markdown: str, tables: list[dict]) -> str:
    table_content = {table["table_id"]: table["content"] for table in tables}

    def replace_table_link(match: re.Match) -> str:
        table_id = match.group(1)
        return table_content.get(table_id, match.group(0))

    return TABLE_LINK_PATTERN.sub(replace_table_link, markdown).strip()


def normalize_page(provider_page: dict, page_num: int) -> dict:
    tables = normalize_tables(provider_page, page_num)
    table_ids = {table["table_id"] for table in tables}
    blocks = []

    for block_index, block in enumerate(provider_page.get("blocks") or [], start=1):
        normalized_block = {
            "block_id": f"p{page_num}_b{block_index:03d}",
            "type": block.get("type") or block.get("label") or "unknown",
            "bbox": bbox_from(block),
            "source_id": block.get("id"),
        }
        if block.get("type") == "table" and block.get("table_id") in table_ids:
            normalized_block["table_id"] = block["table_id"]
        else:
            normalized_block["text"] = (block.get("content") or block.get("text") or "").strip()
        blocks.append(normalized_block)

    confidence = provider_page.get("confidence_scores") or {}
    raw_text = (provider_page.get("markdown") or "").strip()
    return {
        "page_num": page_num,
        "status": "success",
        "dimensions": provider_page.get("dimensions"),
        "confidence": {
            "average": confidence.get("average_page_confidence_score"),
            "minimum": confidence.get("minimum_page_confidence_score"),
        },
        "blocks": blocks,
        "tables": tables,
        "raw_text": raw_text,
        "search_text": build_search_text(raw_text, tables),
    }


def normalize_pages(provider_response: dict, page_count: int) -> list[dict]:
    provider_pages = {
        int(provider_page.get("index", index)) + 1: provider_page
        for index, provider_page in enumerate(provider_response.get("pages") or [])
    }
    pages = []
    for page_num in range(1, page_count + 1):
        provider_page = provider_pages.get(page_num)
        if provider_page is None:
            pages.append(
                {
                    "page_num": page_num,
                    "status": "missing",
                    "dimensions": None,
                    "confidence": {"average": None, "minimum": None},
                    "blocks": [],
                    "tables": [],
                    "raw_text": "",
                    "search_text": "",
                }
            )
        else:
            pages.append(normalize_page(provider_page, page_num))
    return pages


def document_markdown(pages: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"<!-- page {page['page_num']} -->\n{page['search_text']}" for page in pages if page["search_text"]
    ) + "\n"


def document_metrics(pages: list[dict], elapsed_sec: float, cost_per_page_usd: float | None) -> dict:
    page_count = len(pages)
    processed_pages = sum(page["status"] == "success" for page in pages)
    nonempty_pages = sum(bool(page["search_text"]) for page in pages)
    duplicate_pages = sum(has_duplicate_block(page["blocks"]) for page in pages)
    return {
        "processing_success_rate": processed_pages / page_count,
        "page_coverage": nonempty_pages / page_count,
        "empty_output_rate": (page_count - nonempty_pages) / page_count,
        "duplicate_output_rate": duplicate_pages / page_count,
        "schema_valid": len(pages) == page_count,
        "seconds_per_page": elapsed_sec / page_count,
        "cost_per_page_usd": cost_per_page_usd,
    }


def build_normalized_document(
    issuer: str,
    pdf_path: Path,
    provider_response: dict,
    started_at: str,
    finished_at: str,
    elapsed_sec: float,
) -> tuple[dict, str]:
    with fitz.open(pdf_path) as document:
        page_count = len(document)

    pages = normalize_pages(provider_response, page_count)
    search_text = document_markdown(pages)
    cost_per_page = OCR_4_COST_PER_PAGE_USD if provider_response.get("model") == "mistral-ocr-4-0" else None
    metrics = document_metrics(pages, elapsed_sec, cost_per_page)
    for page in pages:
        page.pop("raw_text")
        page.pop("search_text")

    return {
        "schema_version": "1.1",
        "tool": "mistral-ocr-4-0",
        "model": provider_response.get("model"),
        "issuer": issuer,
        "card_name": pdf_path.stem,
        "source_pdf": str(pdf_path.relative_to(ROOT)),
        "started_at": started_at,
        "finished_at": finished_at,
        "page_count": page_count,
        "elapsed_seconds": elapsed_sec,
        "pages": pages,
        "metrics": metrics,
    }, search_text


def request_ocr(pdf_path: Path, api_key: str) -> dict:
    encoded_pdf = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    payload = {
        "model": MODEL,
        "document": {
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{encoded_pdf}",
        },
        "include_blocks": True,
        "include_image_base64": False,
        "table_format": "markdown",
        "confidence_scores_granularity": "page",
    }
    response = requests.post(
        MISTRAL_OCR_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=600,
    )
    if not response.ok:
        raise RuntimeError(f"Mistral API returned HTTP {response.status_code}: {response.text}")
    return response.json()


def output_paths(issuer: str, pdf_path: Path) -> tuple[Path, Path, Path]:
    filename = f"{pdf_path.stem}.json"
    return (
        RAW_OUTPUT_DIR / issuer / filename,
        NORMALIZED_OUTPUT_DIR / issuer / filename,
        TEXT_OUTPUT_DIR / issuer / f"{pdf_path.stem}.md",
    )


def write_outputs(
    issuer: str,
    pdf_path: Path,
    provider_response: dict,
    normalized_document: dict,
    search_text: str,
) -> None:
    raw_path, normalized_path, text_path = output_paths(issuer, pdf_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(provider_response, ensure_ascii=False, indent=2), encoding="utf-8")
    normalized_path.write_text(json.dumps(normalized_document, ensure_ascii=False, indent=2), encoding="utf-8")
    text_path.write_text(search_text, encoding="utf-8")


def migrate_legacy_result(issuer: str, pdf_path: Path) -> bool:
    legacy_path = LEGACY_OUTPUT_DIR / issuer / f"{pdf_path.stem}.json"
    if not legacy_path.exists():
        return False

    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    normalized_document, search_text = build_normalized_document(
        issuer=issuer,
        pdf_path=pdf_path,
        provider_response=legacy["provider_response"],
        started_at=legacy["started_at"],
        finished_at=legacy["finished_at"],
        elapsed_sec=legacy["elapsed_seconds"],
    )
    write_outputs(issuer, pdf_path, legacy["provider_response"], normalized_document, search_text)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mistral OCR 4 on card PDFs.")
    parser.add_argument("--issuer", choices=TARGETS.keys(), help="Run one issuer only. Omit to run all issuers.")
    parser.add_argument(
        "--migrate-legacy",
        action="store_true",
        help="Split existing combined results without calling the API.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.getenv("MISTRAL_API_KEY")
    if not args.migrate_legacy and not api_key:
        print("[ERROR] MISTRAL_API_KEY 환경 변수를 설정한 뒤 실행하세요.")
        return

    targets = {args.issuer: TARGETS[args.issuer]} if args.issuer else TARGETS
    for issuer, filename in targets.items():
        pdf_path = RAW_DIR / issuer / filename

        if args.migrate_legacy:
            print(f"[MIGRATE] {issuer}/{filename}: {migrate_legacy_result(issuer, pdf_path)}")
            continue

        _, normalized_path, _ = output_paths(issuer, pdf_path)
        if normalized_path.exists():
            print(f"[SKIP] existing result: {issuer}/{filename}")
            continue

        print(f"[Mistral OCR 4] {issuer}/{filename}")
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        started = time.perf_counter()
        try:
            provider_response = request_ocr(pdf_path, api_key)
        except Exception as error:
            print(f"[ERROR] {type(error).__name__}: {error}")
            continue

        finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        elapsed_sec = round(time.perf_counter() - started, 3)
        normalized_document, search_text = build_normalized_document(
            issuer=issuer,
            pdf_path=pdf_path,
            provider_response=provider_response,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_sec=elapsed_sec,
        )
        write_outputs(issuer, pdf_path, provider_response, normalized_document, search_text)
        print(normalized_document["metrics"])


if __name__ == "__main__":
    main()
