import sys
import json
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ocr_benchmark"
sys.path.insert(0, str(SCRIPT_DIR))

from run_mistral_ocr import build_search_text, normalize_page, normalize_tables


def sample_page():
    return {
        "index": 0,
        "dimensions": {"width": 1022, "height": 717, "dpi": 91},
        "markdown": "## 연회비\n\n[tbl-0.md](tbl-0.md)\n\n※ 안내문",
        "confidence_scores": {
            "average_page_confidence_score": 0.93,
            "minimum_page_confidence_score": 0.075,
        },
        "tables": [
            {
                "id": "tbl-0.md",
                "format": "markdown",
                "content": "| 구분 | 총연회비 |\n| --- | --- |\n| 국내전용 | 1만 5천원 |",
            }
        ],
        "blocks": [
            {
                "type": "table",
                "table_id": "tbl-0.md",
                "content": "| 구분 | 총연회비 |\n| --- | --- |\n| 국내전용 | 1만 5천원 |",
                "top_left_x": 33,
                "top_left_y": 483,
                "bottom_right_x": 303,
                "bottom_right_y": 539,
            }
        ],
    }


def test_normalize_tables_uses_provider_content_and_table_block_bbox():
    table = normalize_tables(sample_page(), page_num=1)[0]

    assert table == {
        "table_id": "tbl-0.md",
        "format": "markdown",
        "content": "| 구분 | 총연회비 |\n| --- | --- |\n| 국내전용 | 1만 5천원 |",
        "bbox": {"x1": 33.0, "y1": 483.0, "x2": 303.0, "y2": 539.0},
    }


def test_search_text_replaces_mistral_table_link_with_table_content():
    page = sample_page()
    search_text = build_search_text(page["markdown"], normalize_tables(page, page_num=1))

    assert "[tbl-0.md](tbl-0.md)" not in search_text
    assert "| 국내전용 | 1만 5천원 |" in search_text


def test_normalized_page_keeps_minimum_confidence_and_table_reference():
    page = normalize_page(sample_page(), page_num=1)

    assert page["confidence"] == {"average": 0.93, "minimum": 0.075}
    assert page["blocks"][0]["table_id"] == "tbl-0.md"
    assert "text" not in page["blocks"][0]


def test_saved_shinhan_artifacts_preserve_tables_for_search():
    project_root = Path(__file__).resolve().parents[1]
    raw_path = project_root / "data/ocr_benchmark/mistral_raw/shinhan/Shinhan_Toss_Mr.Life_20251231.json"
    normalized_path = project_root / "data/ocr_benchmark/normalized/mistral/shinhan/Shinhan_Toss_Mr.Life_20251231.json"
    text_path = project_root / "data/ocr_benchmark/text/mistral/shinhan/Shinhan_Toss_Mr.Life_20251231.md"

    if not all(path.exists() for path in (raw_path, normalized_path, text_path)):
        pytest.skip("Run the Mistral benchmark before its artifact integration test.")

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    search_text = text_path.read_text(encoding="utf-8")

    raw_table = raw["pages"][0]["tables"][0]
    normalized_table = normalized["pages"][0]["tables"][0]

    assert normalized_table["table_id"] == raw_table["id"]
    assert normalized_table["format"] == raw_table["format"]
    assert normalized_table["content"] == raw_table["content"]
    assert normalized_table["bbox"] == {"x1": 33.0, "y1": 483.0, "x2": 303.0, "y2": 539.0}
    assert "[tbl-0.md](tbl-0.md)" not in search_text
    assert raw_table["content"] in search_text
