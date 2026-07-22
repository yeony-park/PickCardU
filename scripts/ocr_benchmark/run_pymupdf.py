from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "data" / "ocr_benchmark" / "pymupdf"

TARGETS = {
    "BC": "BC_Biz_AirMoney.pdf",
    "NH": "NH_Namu_NH.pdf",
    "hana": "Hana_One_More_SOHO.pdf",
    "hyundai": "Hyundai_The_Orange_20260330.pdf",
    "ibk": "IBK_Point3.8(Credit).pdf",
    "kookmin": "Kookmin_Friend_20210917.pdf",
    "lotte": "Lotte_LOCA_LIKIT_Eat.pdf",
    "samsung": "Samsung_iD_ALL.pdf",
    "shinhan": "Shinhan_Toss_Mr.Life_20251231.pdf",
    "woori": "Woori_Classic_EVERY_MILE_SKYPASS.pdf",
}

def has_duplicate_block(blocks: list[dict]) -> bool:
    texts = [block["text"].strip() for block in blocks if block["text"].strip()]
    return len(texts) != len(set(texts))

def extract_pdf(issuer: str, pdf_path: Path) -> dict:
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    started = time.perf_counter()
    doc = fitz.open(pdf_path)
    pages = []

    try:
        for page_num, page in enumerate(doc, start=1):
            page_result = {
                "page_num": page_num,
                "status": "success",
                "text": "",
                "blocks": [],
                "tables": [],
                "error": None,
                "table_detection_error": None,
            }

            try:
                page_result["text"] = page.get_text("text", sort=True).strip()

                for x0, y0, x1, y1, text, block_no, block_type in page.get_text("blocks", sort=True):
                    if text.strip():
                        page_result["blocks"].append(
                            {
                                "type": "text" if block_type == 0 else "image",
                                "text": text.strip(),
                                "bbox": [x0, y0, x1, y1],
                                "source_id": block_no, 
                            }
                        )

            except Exception as e:
                page_result["status"] = "failed"
                page_result["error"] = f"{type(e).__name__}: {e}"

            if page_result["status"] == "success":
                try:
                    for table in page.find_tables().tables:
                        page_result["tables"].append(
                            {
                                "bbox": list(table.bbox),
                                "cells": table.extract(),
                            }
                        )
                except Exception as e:
                    page_result["table_detection_error"] = f"{type(e).__name__}: {e}"

            pages.append(page_result)

    finally:
        doc.close()

    finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
    elapsed_sec = round(time.perf_counter() - started, 3)
    total_pages = len(pages)
    processed_pages = sum(page["status"] == "success" for page in pages)
    nonempty_pages = sum(bool(page["text"]) for page in pages)
    duplicate_pages = sum(has_duplicate_block(page["blocks"]) for page in pages)

    return {
        "schema_version": "1.0",
        "tool": "pymupdf",
        "issuer": issuer,
        "card_name": pdf_path.stem,
        "source_pdf": str(pdf_path.relative_to(ROOT)),
        "started_at": started_at,
        "finished_at": finished_at,
        "page_count": total_pages,
        "elapsed_seconds": elapsed_sec,
        "pages": pages,
        "metrics": {
            "processing_success_rate": processed_pages / total_pages,
            "page_coverage": nonempty_pages / total_pages,
            "empty_output_rate": (total_pages - nonempty_pages) / total_pages,
            "duplicate_output_rate": duplicate_pages / total_pages,
            "schema_valid": len(pages) == total_pages,
            "seconds_per_page": elapsed_sec / total_pages,
            "cost_per_page_usd": 0.0,
        },
    }

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for issuer, filename in TARGETS.items():
        pdf_path = RAW_DIR / issuer / filename
        print(f"[PyMuPDF] {issuer}/{filename}")

        result = extract_pdf(issuer, pdf_path)
        output_path = OUTPUT_DIR / issuer / f"{pdf_path.stem}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(result["metrics"])


if __name__ == "__main__":
    main()
