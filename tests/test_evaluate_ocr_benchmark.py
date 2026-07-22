import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ocr_benchmark"
sys.path.insert(0, str(SCRIPT_DIR))

from evaluate_ocr_benchmark import markdown_rows, score_numeric_labels, section_order_metrics, text_metrics


def test_numeric_label_requires_the_expected_number_of_occurrences_and_context():
    labels = [
        {
            "id": "international_brand_fee_rate",
            "surface_text": "1.0%",
            "expected_occurrences": 2,
            "context_terms": ["국제브랜드"],
            "critical": True,
        }
    ]

    result = score_numeric_labels(
        {1: "국제브랜드 수수료 1.0% / 국제브랜드 이용수수료율 1.0%"},
        [{**labels[0], "page_num": 1}],
    )

    assert result["numeric_exact_match_rate"] == 1.0
    assert result["relation_context_match_rate"] == 1.0
    assert result["critical_numeric_exact_match_rate"] == 1.0


def test_numeric_label_detects_missing_or_extra_occurrences():
    labels = [
        {
            "id": "annual_fee",
            "surface_text": "5,000원",
            "expected_occurrences": 1,
            "context_terms": ["국내전용(BC)"],
            "critical": True,
        }
    ]

    result = score_numeric_labels({1: "국내전용(BC) 5,000원, 5,000원"}, [{**labels[0], "page_num": 1}])

    assert result["numeric_exact_match_rate"] == 1.0
    assert result["relation_context_match_rate"] == 0.0


def test_text_metrics_are_zero_for_equal_text_after_markdown_normalization():
    result = text_metrics("## 혜택\n\n- 5,000원", "혜택\n5,000원")

    assert result == {"cer": 0.0, "wer": 0.0, "numeric_cer": 0.0, "normalized_edit_distance": 0.0}


def test_markdown_rows_skips_separator_row():
    assert markdown_rows("| 구분 | 내용 |\n| --- | --- |\n| 연회비 | 5,000원 |") == [["구분", "내용"], ["연회비", "5,000원"]]


def test_section_order_requires_all_sections_in_the_right_order():
    result = section_order_metrics("A B C", ["A", "B", "C"])

    assert result == {"section_coverage": 1.0, "section_order_accuracy": 1.0}
