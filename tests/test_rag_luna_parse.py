import sys
from pathlib import Path

import pymupdf
import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

import run_luna_parse as luna
from common import SourceDocument, file_sha256
from run_luna_parse import DPI, MODEL, REASONING, config, render_pdf_pymupdf, valid_pages, visually_uniform_image


def page(number, status="success", markdown="text"):
    return {"page_num": number, "status": status, "markdown": markdown, "uncertain_spans": []}


def rendered_document(tmp_path, styles):
    pdf_path = tmp_path / "source.pdf"
    pdf = pymupdf.open()
    for style in styles:
        page_object = pdf.new_page(width=72, height=72)
        if style == "text":
            page_object.insert_text((8, 36), "CARD 1234", fontsize=12)
        elif style == "solid":
            page_object.draw_rect(page_object.rect, color=(1, 0, 0), fill=(1, 0, 0))
    pdf.save(pdf_path)
    pdf.close()
    images = []
    with pymupdf.open(pdf_path) as source:
        for page_num, page_object in enumerate(source, start=1):
            image_path = tmp_path / f"page-{page_num:04d}.png"
            page_object.get_pixmap(dpi=72, alpha=False).save(image_path)
            images.append(image_path)
    document = SourceDocument(
        "issuer/source",
        "issuer",
        "source",
        pdf_path,
        "source.pdf",
        file_sha256(pdf_path),
        len(styles),
    )
    return document, images


def failed_batch(document, config_sha256, page_start, page_end, pages):
    return {
        "schema_version": "1.0",
        "run_status": "failed",
        "document_id": document.document_id,
        "source_sha256": document.sha256,
        "config_sha256": config_sha256,
        "page_start": page_start,
        "page_end": page_end,
        "usage": {},
        "error": "ValueError: legacy empty-page rejection",
        "output": {"pages": pages},
    }


def test_luna_config_preserves_requested_model_and_dpi():
    value = config(batch_pages=6)

    assert MODEL == "gpt-5.6-luna"
    assert REASONING == "max"
    assert DPI == 200
    assert value["batch_pages"] == 6
    assert value["renderer"] == "pymupdf"


def test_completed_page_batch_requires_exact_order_success_and_valid_types():
    assert valid_pages([page(3), page(4)], [3, 4])
    assert not valid_pages([page(4), page(3)], [3, 4])
    assert not valid_pages([page(3), page(4, status="failed")], [3, 4])
    assert valid_pages([page(3), page(4, markdown="")], [3, 4])
    blank = {**page(4, markdown=""), "is_blank": True}
    assert valid_pages([page(3), blank], [3, 4])


def test_pymupdf_renderer_creates_readable_200_dpi_image(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf = pymupdf.open()
    page_object = pdf.new_page(width=72, height=72)
    page_object.insert_text((8, 36), "CARD 1234", fontsize=12)
    pdf.save(pdf_path)
    pdf.close()
    document = SourceDocument(
        document_id="issuer/sample",
        issuer="issuer",
        card_name="sample",
        path=pdf_path,
        relative_path="sample.pdf",
        sha256=file_sha256(pdf_path),
        page_count=1,
    )

    images, blank_pages = render_pdf_pymupdf(document, 200, tmp_path / "render-cache")

    assert len(images) == 1
    assert blank_pages == set()
    pixmap = pymupdf.Pixmap(images[0])
    assert pixmap.width == 200
    assert pixmap.height == 200
    assert min(pixmap.samples) < 250


def test_pymupdf_renderer_marks_a_truly_blank_page(tmp_path):
    pdf_path = tmp_path / "blank.pdf"
    pdf = pymupdf.open()
    pdf.new_page(width=72, height=72)
    pdf.save(pdf_path)
    pdf.close()
    document = SourceDocument(
        document_id="issuer/blank",
        issuer="issuer",
        card_name="blank",
        path=pdf_path,
        relative_path="blank.pdf",
        sha256=file_sha256(pdf_path),
        page_count=1,
    )

    images, blank_pages = render_pdf_pymupdf(document, 200, tmp_path / "render-cache")

    assert len(images) == 1
    assert blank_pages == {1}


def test_uniform_colored_page_is_recognized_as_textless(tmp_path):
    pdf_path = tmp_path / "solid.pdf"
    pdf = pymupdf.open()
    page_object = pdf.new_page(width=72, height=72)
    page_object.draw_rect(page_object.rect, color=(1, 0, 0), fill=(1, 0, 0))
    pdf.save(pdf_path)
    pdf.close()
    document = SourceDocument(
        document_id="issuer/solid",
        issuer="issuer",
        card_name="solid",
        path=pdf_path,
        relative_path="solid.pdf",
        sha256=file_sha256(pdf_path),
        page_count=1,
    )

    images, _ = render_pdf_pymupdf(document, 200, tmp_path / "render-cache")

    assert visually_uniform_image(images[0]) is True


def test_failed_multi_page_batch_falls_back_to_individual_pages(monkeypatch):
    document = SourceDocument("issuer/card", "issuer", "card", Path("card.pdf"), "card.pdf", "sha", 2)
    calls = []

    def fake_run_batch(
        _document,
        images,
        page_start,
        _blank_page_numbers,
        _config_sha256,
        _timeout,
        _max_attempts,
        _force,
    ):
        calls.append((len(images), page_start))
        if len(images) > 1:
            return {"run_status": "failed", "page_start": page_start, "page_end": page_start + 1, "error": "bad batch"}
        return {
            "run_status": "completed",
            "page_start": page_start,
            "page_end": page_start,
            "pages": [page(page_start)],
        }

    monkeypatch.setattr(luna, "run_batch", fake_run_batch)

    effective, attempts, recovery = luna.run_batch_with_page_fallback(
        document, [Path("1.png"), Path("2.png")], 1, set(), "config", 10, 1, True
    )

    assert calls == [(2, 1), (1, 1), (1, 2)]
    assert [artifact["pages"][0]["page_num"] for artifact in effective] == [1, 2]
    assert len(attempts) == 3
    assert recovery["status"] == "page_fallback"


def test_known_blank_page_is_synthesized_without_requiring_model_output(tmp_path, monkeypatch):
    document = SourceDocument("issuer/card", "issuer", "card", Path("card.pdf"), "card.pdf", "sha", 2)

    def fake_run_cli(_condition, images, _prompt, _timeout):
        assert images == [Path("1.png")]
        return {"pages": [page(1)]}, {"events": []}

    monkeypatch.setattr(luna, "run_cli", fake_run_cli)
    monkeypatch.setattr(luna, "usage_from_raw", lambda _condition, _raw: {})
    monkeypatch.setattr(luna, "batch_path", lambda _document, _start, _end: tmp_path / "batch.json")

    artifact = luna.run_batch(
        document,
        [Path("1.png"), Path("2.png")],
        1,
        {2},
        "config",
        10,
        1,
        True,
    )

    assert artifact["run_status"] == "completed"
    assert [value["page_num"] for value in artifact["pages"]] == [1, 2]
    assert artifact["pages"][1]["is_blank"] is True
    assert artifact["pages"][1]["markdown"] == ""


def test_failed_batch_with_valid_output_is_promoted_without_external_call(tmp_path, monkeypatch):
    document, images = rendered_document(tmp_path, ["white", "white", "text", "solid"])
    destination = tmp_path / "batch.json"
    luna.write_json(
        destination,
        failed_batch(
            document,
            "config",
            3,
            4,
            [page(99, markdown="CARD 1234"), page(42, markdown="")],
        ),
    )
    monkeypatch.setattr(luna, "batch_path", lambda _document, _start, _end: destination)

    def unexpected_external_call(*_args, **_kwargs):
        raise AssertionError("a reusable failed artifact must not call Luna")

    monkeypatch.setattr(luna, "run_cli", unexpected_external_call)

    artifact = luna.run_batch(document, images[2:], 3, set(), "config", 10, 1, False)

    assert artifact["run_status"] == "completed"
    assert [value["page_num"] for value in artifact["pages"]] == [3, 4]
    assert artifact["pages"][0]["is_blank"] is False
    assert artifact["pages"][1]["is_blank"] is True
    assert artifact["recovered_from_failed_artifact"]["error"] == "ValueError: legacy empty-page rejection"
    assert luna.read_json(destination)["run_status"] == "completed"


def test_failed_batch_recovery_synthesizes_known_blank_and_maps_inference_page(tmp_path):
    document, images = rendered_document(tmp_path, ["solid", "text"])
    destination = tmp_path / "batch.json"
    luna.write_json(
        destination,
        failed_batch(document, "config", 1, 2, [page(77, markdown="CARD 1234")]),
    )

    artifact = luna.load_batch(destination, document, "config", [1, 2], images, {1})

    assert artifact is not None
    assert artifact["synthetic_blank_pages"] == [1]
    assert artifact["pages"] == [
        luna.synthetic_blank_page(1),
        {**page(2, markdown="CARD 1234"), "is_blank": False},
    ]


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("document_id", "other/card"),
        ("source_sha256", "other-source"),
        ("config_sha256", "other-config"),
        ("page_start", 0),
        ("page_end", 3),
    ],
)
def test_failed_batch_with_mismatched_identity_is_not_promoted(tmp_path, field, invalid_value):
    document, images = rendered_document(tmp_path, ["text", "solid"])
    destination = tmp_path / "batch.json"
    value = failed_batch(document, "config", 1, 2, [page(1), page(2, markdown="")])
    value[field] = invalid_value
    luna.write_json(destination, value)

    assert luna.load_batch(destination, document, "config", [1, 2], images, set()) is None
    assert luna.read_json(destination)["run_status"] == "failed"


@pytest.mark.parametrize(
    "output_pages",
    [
        [page(1), page(2, status="failed")],
        [page(1)],
        [page(1), {**page(2), "markdown": None}],
    ],
)
def test_failed_batch_with_invalid_output_is_not_promoted(tmp_path, output_pages):
    document, images = rendered_document(tmp_path, ["text", "solid"])
    destination = tmp_path / "batch.json"
    luna.write_json(destination, failed_batch(document, "config", 1, 2, output_pages))

    assert luna.load_batch(destination, document, "config", [1, 2], images, set()) is None
    assert luna.read_json(destination)["run_status"] == "failed"
