import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

from verification import compare_page, verify_document


def primary_document(text):
    return {
        "document_id": "issuer/card",
        "source": {"sha256": "abc", "page_count": 1, "issuer": "issuer", "card_name": "card", "path": "data/raw/x.pdf"},
        "parser": {"model": "gpt-5.6-luna", "dpi": 200},
        "run_status": "completed",
        "pages": [{"page_num": 1, "status": "success", "markdown": text, "uncertain_spans": []}],
    }


def layout_document(text, block_type="paragraph", bbox=True):
    return {
        "document_id": "issuer/card",
        "source": {"sha256": "abc", "page_count": 1, "issuer": "issuer", "card_name": "card", "path": "data/raw/x.pdf"},
        "parser": {"model": "document-parse"},
        "run_status": "completed",
        "pages": [
            {
                "page_num": 1,
                "status": "success",
                "coordinate_space": "normalized_0_1",
                "blocks": [
                    {
                        "block_id": "b1",
                        "reading_order": 1,
                        "type": block_type,
                        "text": text,
                        "bbox": {"x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.2} if bbox else None,
                    }
                ],
                "tables": [],
            }
        ],
    }


def test_verification_keeps_luna_as_resolved_text():
    primary = primary_document("할인율 10%")
    layout = layout_document("할인율 20%")

    result = verify_document(primary, layout)

    assert result["pages"][0]["resolved_text"] == "할인율 10%"
    assert result["pages"][0]["verification"]["verdict"] == "review_required"


def test_structural_issue_defers_pp_structure_only_for_flagged_page():
    primary = primary_document("제목\n\n본문")
    layout = layout_document("완전히 다른 표제", block_type="heading1", bbox=False)

    result = verify_document(primary, layout)

    assert result["pp_structure_v3"]["status"] == "deferred"
    assert result["pp_structure_v3"]["pages"] == [1]
    assert "bbox_coverage_low" in result["pages"][0]["verification"]["issues"]


def test_equal_text_page_passes():
    comparison = compare_page(primary_document("연회비 10,000원")["pages"][0], layout_document("연회비 10,000원")["pages"][0])

    assert comparison["verdict"] == "pass"
    assert comparison["metrics"]["numeric_f1"] == 1.0


def test_confirmed_blank_page_passes_when_layout_is_also_empty():
    primary = primary_document("")
    primary["pages"][0]["is_blank"] = True

    result = verify_document(primary, layout_document(""))

    assert result["verdict"] == "pass"
    assert result["pages"][0]["primary"]["is_blank"] is True


def test_blank_primary_with_nonempty_layout_is_deferred_for_structure_review():
    primary = primary_document("")
    primary["pages"][0]["is_blank"] = True

    result = verify_document(primary, layout_document("숨은 내용"))

    assert result["pp_structure_v3"]["status"] == "deferred"
    assert result["pp_structure_v3"]["pages"] == [1]


def test_empty_layout_for_nonblank_primary_is_deferred_for_structure_review():
    result = verify_document(primary_document("본문이 있는 페이지"), layout_document(""))

    assert "empty_layout_page" in result["pages"][0]["verification"]["issues"]
    assert result["pp_structure_v3"]["status"] == "deferred"
    assert result["pp_structure_v3"]["pages"] == [1]


def test_empty_primary_and_locally_derived_blank_layout_passes_with_provenance():
    primary = primary_document("")
    layout = layout_document("")
    layout_page = layout["pages"][0]
    layout_page["is_blank"] = True
    layout_page["blocks"] = []
    layout_page["blank_provenance"] = {
        "method": "dominant_rendered_rgb",
        "dominant_rgb_ratio": 0.998,
        "native_text": "2",
        "image_count": 0,
        "drawing_count": 1,
    }

    result = verify_document(primary, layout)

    assert result["verdict"] == "pass"
    assert result["pages"][0]["verification"]["metrics"]["layout_derived_blank"] is True
    assert result["pages"][0]["layout"]["is_blank"] is True
    assert result["pages"][0]["layout"]["blank_provenance"] == layout_page["blank_provenance"]
