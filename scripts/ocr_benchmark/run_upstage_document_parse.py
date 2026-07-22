from __future__ import annotations

import argparse
import json
import os
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
RAW_OUTPUT_DIR = BENCHMARK_DIR / "upstage_raw"
NORMALIZED_OUTPUT_DIR = BENCHMARK_DIR / "normalized" / "upstage"
TEXT_OUTPUT_DIR = BENCHMARK_DIR / "text" / "upstage"
TEMP_PDF_DIR = ROOT / "tmp" / "pdfs"
UPSTAGE_URL = "https://api.upstage.ai/v1/document-digitization"
MODEL = "document-parse"
UPSTAGE_COST_PER_PAGE_USD = 0.01


def has_duplicate_block(blocks: list[dict]) -> bool:
    texts = [block.get("text", "").strip() for block in blocks if block.get("text", "").strip()]
    return len(texts) != len(set(texts))


def bbox_from_coordinates(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict) and all(key in value for key in ("x", "y")):
        return {"x1": float(value["x"]), "y1": float(value["y"]), "x2": float(value["x"]), "y2": float(value["y"])}
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


def element_text(element: dict) -> str:
    content = element.get("content")
    if isinstance(content, dict):
        return (content.get("markdown") or content.get("html") or content.get("text") or "").strip()
    return str(content or element.get("markdown") or element.get("html") or element.get("text") or "").strip()


def provider_elements(provider_response: dict) -> list[dict]:
    if isinstance(provider_response.get("elements"), list):
        return provider_response["elements"]
    content = provider_response.get("content")
    if isinstance(content, dict) and isinstance(content.get("elements"), list):
        return content["elements"]
    return []


def document_markdown(provider_response: dict, pages: list[dict]) -> str:
    content = provider_response.get("content")
    if isinstance(content, dict) and content.get("markdown"):
        return content["markdown"].strip() + "\n"
    return "\n\n---\n\n".join(
        f"<!-- page {page['page_num']} -->\n{page['search_text']}" for page in pages if page["search_text"]
    ) + "\n"


def normalize_pages(provider_response: dict, page_count: int) -> list[dict]:
    pages = {
        page_num: {
            "page_num": page_num,
            "status": "success",
            "dimensions": None,
            "blocks": [],
            "tables": [],
            "search_text": [],
        }
        for page_num in range(1, page_count + 1)
    }

    for element_index, element in enumerate(provider_elements(provider_response), start=1):
        page_num = int(element.get("page") or element.get("page_number") or 1)
        if page_num not in pages:
            continue
        page = pages[page_num]
        element_type = element.get("category") or element.get("type") or "unknown"
        text = element_text(element)
        bbox = bbox_from_coordinates(element.get("coordinates") or element.get("bounding_box") or element.get("bbox"))
        source_id = element.get("id")

        if str(element_type).lower() == "table":
            table_id = str(source_id or f"p{page_num}_t{len(page['tables']) + 1:03d}")
            page["tables"].append(
                {
                    "table_id": table_id,
                    "format": "markdown" if "|" in text else "html",
                    "content": text,
                    "bbox": bbox,
                }
            )
            page["blocks"].append(
                {
                    "block_id": f"p{page_num}_b{element_index:03d}",
                    "type": "table",
                    "table_id": table_id,
                    "bbox": bbox,
                    "source_id": source_id,
                }
            )
        else:
            page["blocks"].append(
                {
                    "block_id": f"p{page_num}_b{element_index:03d}",
                    "type": element_type,
                    "text": text,
                    "bbox": bbox,
                    "source_id": source_id,
                }
            )
        if text:
            page["search_text"].append(text)

    normalized_pages = []
    for page in pages.values():
        page["search_text"] = "\n\n".join(page["search_text"])
        normalized_pages.append(page)
    return normalized_pages


def document_metrics(pages: list[dict], elapsed_sec: float) -> dict:
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
        "cost_per_page_usd": UPSTAGE_COST_PER_PAGE_USD,
    }


def build_normalized_document(
    issuer: str,
    pdf_path: Path,
    provider_response: dict,
    started_at: str,
    finished_at: str,
    elapsed_sec: float,
    page_count: int,
) -> tuple[dict, str]:
    pages = normalize_pages(provider_response, page_count)
    search_text = document_markdown(provider_response, pages)
    metrics = document_metrics(pages, elapsed_sec)
    for page in pages:
        page.pop("search_text")

    return {
        "schema_version": "1.1",
        "tool": "upstage-document-parse",
        "model": MODEL,
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


def request_parse(pdf_path: Path, api_key: str) -> dict:
    with pdf_path.open("rb") as document:
        response = requests.post(
            UPSTAGE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"document": (pdf_path.name, document, "application/pdf")},
            data={
                "model": MODEL,
                "ocr": "force",
                "coordinates": "true",
                "output_formats": '["html", "markdown"]',
            },
            timeout=600,
        )
    if not response.ok:
        raise RuntimeError(f"Upstage API returned HTTP {response.status_code}: {response.text}")
    return response.json()


def output_paths(issuer: str, pdf_path: Path, output_stem: str | None = None) -> tuple[Path, Path, Path]:
    name = output_stem or pdf_path.stem
    filename = f"{name}.json"
    return (
        RAW_OUTPUT_DIR / issuer / filename,
        NORMALIZED_OUTPUT_DIR / issuer / filename,
        TEXT_OUTPUT_DIR / issuer / f"{name}.md",
    )


def write_outputs(
    issuer: str,
    pdf_path: Path,
    provider_response: dict,
    normalized_document: dict,
    search_text: str,
    output_stem: str | None = None,
) -> None:
    raw_path, normalized_path, text_path = output_paths(issuer, pdf_path, output_stem)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(provider_response, ensure_ascii=False, indent=2), encoding="utf-8")
    normalized_path.write_text(json.dumps(normalized_document, ensure_ascii=False, indent=2), encoding="utf-8")
    text_path.write_text(search_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Upstage Document Parse on card PDFs.")
    parser.add_argument("--issuer", choices=TARGETS.keys(), help="Run one issuer only. Omit to run all issuers.")
    parser.add_argument("--max-pages", type=int, help="Upload only the first N pages for a paid API smoke test.")
    args = parser.parse_args()
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    return args


def create_subset_pdf(pdf_path: Path, page_count: int) -> Path:
    TEMP_PDF_DIR.mkdir(parents=True, exist_ok=True)
    subset_path = TEMP_PDF_DIR / f"{pdf_path.stem}_pages1-{page_count}.pdf"
    with fitz.open(pdf_path) as source, fitz.open() as subset:
        subset.insert_pdf(source, from_page=0, to_page=page_count - 1)
        subset.save(subset_path)
    return subset_path


def main() -> None:
    args = parse_args()
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        print("[ERROR] UPSTAGE_API_KEY 환경 변수를 설정한 뒤 실행하세요.")
        return

    targets = {args.issuer: TARGETS[args.issuer]} if args.issuer else TARGETS
    for issuer, filename in targets.items():
        pdf_path = RAW_DIR / issuer / filename
        with fitz.open(pdf_path) as document:
            total_pages = len(document)
        page_count = min(args.max_pages or total_pages, total_pages)
        output_stem = f"{pdf_path.stem}_pages1-{page_count}" if page_count < total_pages else None
        _, normalized_path, _ = output_paths(issuer, pdf_path, output_stem)
        if normalized_path.exists():
            print(f"[SKIP] existing result: {issuer}/{filename}")
            continue

        upload_path = create_subset_pdf(pdf_path, page_count) if page_count < total_pages else pdf_path
        print(f"[Upstage Document Parse] {issuer}/{filename} ({page_count} page(s))")
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        started = time.perf_counter()
        try:
            provider_response = request_parse(upload_path, api_key)
        except Exception as error:
            print(f"[ERROR] {type(error).__name__}: {error}")
            if upload_path != pdf_path:
                upload_path.unlink(missing_ok=True)
            continue

        if upload_path != pdf_path:
            upload_path.unlink(missing_ok=True)

        finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        elapsed_sec = round(time.perf_counter() - started, 3)
        normalized_document, search_text = build_normalized_document(
            issuer, pdf_path, provider_response, started_at, finished_at, elapsed_sec, page_count
        )
        write_outputs(issuer, pdf_path, provider_response, normalized_document, search_text, output_stem)
        print(normalized_document["metrics"])


if __name__ == "__main__":
    main()
