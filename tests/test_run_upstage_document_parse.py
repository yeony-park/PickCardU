import sys
import json
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ocr_benchmark"
sys.path.insert(0, str(SCRIPT_DIR))

from run_upstage_document_parse import bbox_from_coordinates, normalize_pages, parse_args


def sample_response():
    return {
        "content": {"markdown": "## 연회비\n\n| 구분 | 연회비 |\n| --- | --- |\n| 국내전용 | 1만 5천원 |"},
        "elements": [
            {
                "id": "table-1",
                "page": 1,
                "category": "table",
                "content": {"markdown": "| 구분 | 연회비 |\n| --- | --- |\n| 국내전용 | 1만 5천원 |"},
                "coordinates": [
                    {"x": 33, "y": 483},
                    {"x": 303, "y": 483},
                    {"x": 303, "y": 539},
                    {"x": 33, "y": 539},
                ],
            }
        ],
    }


def test_bbox_from_coordinate_vertices():
    assert bbox_from_coordinates(sample_response()["elements"][0]["coordinates"]) == {
        "x1": 33.0,
        "y1": 483.0,
        "x2": 303.0,
        "y2": 539.0,
    }


def test_table_element_becomes_table_with_bbox_and_block_reference():
    page = normalize_pages(sample_response(), page_count=1)[0]

    assert page["tables"] == [
        {
            "table_id": "table-1",
            "format": "markdown",
            "content": "| 구분 | 연회비 |\n| --- | --- |\n| 국내전용 | 1만 5천원 |",
            "bbox": {"x1": 33.0, "y1": 483.0, "x2": 303.0, "y2": 539.0},
        }
    ]
    assert page["blocks"][0]["table_id"] == "table-1"
    assert "text" not in page["blocks"][0]


def test_max_pages_must_be_positive(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_upstage_document_parse.py", "--max-pages", "0"])

    try:
        parse_args()
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("--max-pages 0 should be rejected")


def test_saved_shinhan_result_preserves_coordinates_and_tables():
    result_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "ocr_benchmark"
        / "normalized"
        / "upstage"
        / "shinhan"
        / "Shinhan_Toss_Mr.Life_20251231.json"
    )
    if not result_path.exists():
        pytest.skip("Upstage API result has not been created yet")

    document = json.loads(result_path.read_text(encoding="utf-8"))
    tables = [table for page in document["pages"] for table in page["tables"]]
    blocks = [block for page in document["pages"] for block in page["blocks"]]

    assert document["page_count"] == 2
    assert all(block["bbox"] is not None for block in blocks)
    assert tables
    assert all(table["content"] and table["bbox"] is not None for table in tables)
