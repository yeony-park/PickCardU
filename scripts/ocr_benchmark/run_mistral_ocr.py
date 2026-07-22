from __future__ import annotations

import base64
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
import requests

from run_pymupdf import RAW_DIR, ROOT, TARGETS


OUTPUT_DIR = ROOT / "data" / "ocr_benchmark" / "mistral"
MISTRAL_OCR_URL = "https://api.mistral.ai/v1/ocr"
MODEL = "mistral-ocr-4-0"


def bbox_from(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return [float(number) for number in value]
    if isinstance(value, dict) and all(key in value for key in ("x0", "y0", "x1", "y1")):
        return [float(value[key]) for key in ("x0", "y0", "x1", "y1")]
    return None


def has_duplicate_block(blocks: list[dict]) -> bool:
    texts = [block["text"].strip() for block in blocks if block["text"].strip()]
    return len(texts) != len(set(texts))


def empty_page(page_num: int) -> dict:
    return {
        "page_num": page_num,
        "status": "missing",
        "text": "",
        "blocks": [],
        "tables": [],
        "confidence": None,
        "error": None,
    }


def extract_pdf(issuer: str, pdf_path: Path, api_key: str) -> dict:
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    started = time.perf_counter()

    with fitz.open(pdf_path) as document:
        page_count = len(document)

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
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=600,
    )
    response.raise_for_status()
    provider_response = response.json()

    pages_by_number: dict[int, dict] = {}
    for provider_page in provider_response.get("pages", []):
        page_num = int(provider_page.get("index", len(pages_by_number))) + 1
        page_result = empty_page(page_num)
        page_result["status"] = "success"
        page_result["text"] = (provider_page.get("markdown") or "").strip()

        confidence = provider_page.get("confidence_scores") or {}
        page_result["confidence"] = confidence.get("average_page_confidence_score")

        for block in provider_page.get("blocks") or []:
            page_result["blocks"].append(
                {
                    "type": block.get("type") or block.get("label") or "unknown",
                    "text": (block.get("content") or block.get("text") or "").strip(),
                    "bbox": bbox_from(block.get("bbox") or block.get("bounding_box")),
                    "source_id": block.get("id"),
                }
            )

        for table in provider_page.get("tables") or []:
            page_result["tables"].append(
                {
                    "markdown": table.get("markdown"),
                    "html": table.get("html"),
                    "bbox": bbox_from(table.get("bbox") or table.get("bounding_box")),
                }
            )

        pages_by_number[page_num] = page_result

    pages = [pages_by_number.get(page_num, empty_page(page_num)) for page_num in range(1, page_count + 1)]
    finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
    elapsed_sec = round(time.perf_counter() - started, 3)

    processed_pages = sum(page["status"] == "success" for page in pages)
    nonempty_pages = sum(bool(page["text"]) for page in pages)
    duplicate_pages = sum(has_duplicate_block(page["blocks"]) for page in pages)

    return {
        "schema_version": "1.0",
        "tool": "mistral-ocr-4-0",
        "issuer": issuer,
        "card_name": pdf_path.stem,
        "source_pdf": str(pdf_path.relative_to(ROOT)),
        "started_at": started_at,
        "finished_at": finished_at,
        "page_count": page_count,
        "elapsed_seconds": elapsed_sec,
        "pages": pages,
        "provider_response": provider_response,
        "metrics": {
            "processing_success_rate": processed_pages / page_count,
            "page_coverage": nonempty_pages / page_count,
            "empty_output_rate": (page_count - nonempty_pages) / page_count,
            "duplicate_output_rate": duplicate_pages / page_count,
            "schema_valid": len(pages) == page_count,
            "seconds_per_page": elapsed_sec / page_count,
            "cost_per_page_usd": None,
        },
    }


def main():
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        print("[ERROR] MISTRAL_API_KEY 환경 변수를 설정한 뒤 실행하세요.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for issuer, filename in TARGETS.items():
        pdf_path = RAW_DIR / issuer / filename
        print(f"[Mistral OCR 4] {issuer}/{filename}")

        try:
            result = extract_pdf(issuer, pdf_path, api_key)
        except Exception as error:
            print(f"[ERROR] {type(error).__name__}: {error}")
            continue

        output_path = OUTPUT_DIR / issuer / f"{pdf_path.stem}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(result["metrics"])


if __name__ == "__main__":
    main()
