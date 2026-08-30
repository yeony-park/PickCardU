import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ocr_benchmark"
sys.path.insert(0, str(SCRIPT_DIR))

from evaluate_openai_ocr_benchmark import markdown_tables, raw_page_texts, table_similarity


def test_raw_page_texts_accepts_markers_with_or_without_spaces():
    raw = "[page 1]\n첫째\n[page2]\n둘째"

    assert raw_page_texts(raw) == {1: "첫째", 2: "둘째"}


def test_markdown_tables_extracts_multiple_tables():
    text = "앞\n| A | B |\n| --- | --- |\n| 1 | 2 |\n뒤\n| C | D |\n| --- | --- |\n| 3 | 4 |"

    assert markdown_tables(text) == [
        [["A", "B"], ["1", "2"]],
        [["C", "D"], ["3", "4"]],
    ]


def test_table_similarity_is_one_for_equal_tables():
    table = [["구분", "금액"], ["국내", "5,000원"]]

    assert table_similarity(table, table) == {"content": 1.0, "structure": 1.0}
