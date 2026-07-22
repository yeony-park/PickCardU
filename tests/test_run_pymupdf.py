import sys
from pathlib import Path

import fitz

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ocr_benchmark"
sys.path.insert(0, str(SCRIPT_DIR))

from run_pymupdf import RAW_DIR, extract_pdf, has_duplicate_block

# 현재는 테스트 파일들만 실행하는 중이라 테스트마다 경로를 만들도록 그냥 둠
# 추후에 pdf 경로 fixture로 분리해야 함

def test_duplicate_block_is_detected():
    blocks = [
        {"text": "전월 실적 30만원 이상"},
        {"text": "전월 실적 30만원 이상"},
    ]

    assert has_duplicate_block(blocks) is True


def test_unique_blocks_are_not_duplicate():
    blocks = [
        {"text": "전월 실적 30만원 이상"},
        {"text": "월 할인 한도 1만원"},
    ]

    assert has_duplicate_block(blocks) is False


def test_mr_life_extraction_has_valid_page_schema():
    pdf_path = RAW_DIR / "shinhan" / "Shinhan_Toss_Mr.Life_20251231.pdf"

    result = extract_pdf("shinhan", pdf_path)

    with fitz.open(pdf_path) as pdf:
        expected_page_count = len(pdf)

    assert result["tool"] == "pymupdf"
    assert result["page_count"] == expected_page_count
    assert len(result["pages"]) == expected_page_count
    assert all(page["status"] in {"success", "failed"} for page in result["pages"])
    assert all("text" in page and "blocks" in page and "tables" in page for page in result["pages"])
