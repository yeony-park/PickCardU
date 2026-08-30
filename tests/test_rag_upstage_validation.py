import copy
import json
import sys
import urllib.error
from contextlib import nullcontext
from pathlib import Path

import pymupdf
import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

import run_upstage_validation as upstage
from common import SourceDocument, read_json, value_sha256
from run_upstage_validation import (
    UpstageHTTPError,
    is_retryable_error,
    multipart_body,
    migrate_v3_artifact,
    normalize_pages,
    recoverable_failed_artifact,
    source_page_contentless_evidence,
    validate_provider_response,
    valid_layout_pages,
    validate_resolved_models,
)


def test_upstage_normalization_preserves_layout_order_bbox_and_table(tmp_path):
    response = {
        "elements": [
            {
                "id": 7,
                "page": 1,
                "category": "heading1",
                "content": {"markdown": "# 제목"},
                "coordinates": [{"x": 0.1, "y": 0.2}, {"x": 0.7, "y": 0.3}],
            },
            {
                "id": 8,
                "page": 1,
                "category": "table",
                "content": {"markdown": "| A | B |\n|---|---|\n| 1 | 2 |"},
                "coordinates": [{"x": 0.1, "y": 0.4}, {"x": 0.9, "y": 0.8}],
            },
        ]
    }

    pages = normalize_pages(response, 1)

    assert pages[0]["coordinate_space"] == "normalized_0_1"
    assert [block["reading_order"] for block in pages[0]["blocks"]] == [1, 2]
    assert pages[0]["blocks"][0]["bbox"] == {"x1": 0.1, "y1": 0.2, "x2": 0.7, "y2": 0.3}
    assert pages[0]["blocks"][1]["table_id"] == "8"
    assert pages[0]["tables"][0]["content"].startswith("| A")


def test_multipart_body_contains_fields_and_document(tmp_path):
    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-sample")

    body, boundary = multipart_body({"model": "document-parse", "ocr": "force"}, document)

    assert boundary.encode() in body
    assert b'name="model"' in body
    assert b"document-parse" in body
    assert b'filename="sample.pdf"' in body
    assert b"%PDF-sample" in body


def test_empty_provider_response_is_not_valid_for_nonblank_pages():
    pages = normalize_pages({}, 2)

    assert not valid_layout_pages(pages, 2)


def test_confirmed_blank_page_may_have_no_layout_blocks():
    pages = normalize_pages({}, 1, {1})

    assert valid_layout_pages(pages, 1)


def test_nonblank_page_requires_a_meaningful_layout_block():
    pages = normalize_pages(
        {"elements": [{"page": 1, "category": "unknown", "content": {"markdown": ""}}]},
        1,
    )

    assert not valid_layout_pages(pages, 1)


def test_bbox_only_block_is_meaningful_layout_structure():
    pages = normalize_pages(
        {
            "elements": [
                {
                    "page": 1,
                    "category": "figure",
                    "content": {"markdown": ""},
                    "coordinates": [{"x": 0.1, "y": 0.2}, {"x": 0.8, "y": 0.9}],
                }
            ]
        },
        1,
    )

    assert valid_layout_pages(pages, 1)


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("connection reset"),
        TimeoutError("timed out"),
        UpstageHTTPError(408, "timeout"),
        UpstageHTTPError(429, "rate limited"),
        UpstageHTTPError(500, "server error"),
    ],
)
def test_only_transport_and_transient_http_errors_are_retryable(error):
    assert is_retryable_error(error)


@pytest.mark.parametrize("error", [UpstageHTTPError(400, "bad request"), UpstageHTTPError(401, "unauthorized"), ValueError("bad schema")])
def test_permanent_http_and_schema_errors_are_not_retryable(error):
    assert not is_retryable_error(error)


def make_document(tmp_path: Path, page_count: int = 1) -> SourceDocument:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-sample")
    return SourceDocument(
        document_id="issuer/card",
        issuer="issuer",
        card_name="card",
        path=path,
        relative_path="source.pdf",
        sha256="source-sha",
        page_count=page_count,
    )


def valid_response(page_count: int = 1, omitted_pages: set[int] | None = None) -> dict:
    omitted_pages = omitted_pages or set()
    return {
        "api": "2.0",
        "model": "document-parse-260128",
        "usage": {"pages": page_count},
        "elements": [
            {
                "page": page_num,
                "category": "paragraph",
                "content": {"markdown": f"{page_num}쪽 연회비 10,000원"},
                "coordinates": [{"x": 0.1, "y": 0.2}, {"x": 0.8, "y": 0.3}],
            }
            for page_num in range(1, page_count + 1)
            if page_num not in omitted_pages
        ],
    }


def prepare_process_test(
    tmp_path: Path,
    monkeypatch,
    page_count: int = 1,
    blank_pages: set[int] | None = None,
) -> SourceDocument:
    blank_pages = blank_pages or set()
    document = make_document(tmp_path, page_count)
    monkeypatch.setattr(upstage, "OUTPUT_DIR", tmp_path / "upstage")
    monkeypatch.setattr(upstage, "RAW_OUTPUT_DIR", tmp_path / "upstage_raw")
    monkeypatch.setattr(upstage, "PRIMARY_DIR", tmp_path / "luna")
    monkeypatch.setattr(upstage, "complete_luna_artifact", lambda *_args: True)
    monkeypatch.setattr(upstage.time, "sleep", lambda _seconds: None)
    primary = upstage.PRIMARY_DIR / "issuer" / "card.json"
    primary.parent.mkdir(parents=True)
    primary.write_text(
        json.dumps(
            {
                "parser": {"batch_pages": 6},
                "pages": [
                    {"page_num": page_num, "is_blank": page_num in blank_pages}
                    for page_num in range(1, page_count + 1)
                ],
            }
        ),
        encoding="utf-8",
    )
    return document


def failed_candidate(document: SourceDocument, raw: dict, attempt_count: int = 1) -> dict:
    parser_config = upstage.config()
    cumulative_cost = round(attempt_count * document.page_count * upstage.COST_PER_PAGE_USD, 4)
    return {
        "schema_version": "2.0",
        "document_id": document.document_id,
        "source": document.as_dict(),
        "parser": {
            **parser_config,
            "resolved_model": raw["model"],
            "provider_api_version": raw["api"],
            "raw_response_sha256": value_sha256(raw),
            "config_sha256": value_sha256(parser_config),
        },
        "run_status": "failed",
        "started_at": "2026-08-19T10:00:00+09:00",
        "finished_at": "2026-08-19T10:00:01+09:00",
        "elapsed_seconds": 1.0,
        "attempt_count": attempt_count,
        "current_run_attempt_count": attempt_count,
        "estimated_cost_usd": cumulative_cost,
        "cumulative_estimated_cost_usd": cumulative_cost,
        "estimated_cost_basis": "submitted_attempts",
        "error": "ValueError: incomplete layout",
    }


def legacy_candidate(
    document: SourceDocument,
    raw: dict,
    run_status: str = "completed",
    attempt_count: int = 1,
) -> dict:
    artifact = failed_candidate(document, raw, attempt_count)
    parser_config = upstage.config(upstage.LEGACY_NORMALIZER_VERSION)
    artifact["parser"] = {
        **parser_config,
        "resolved_model": raw["model"],
        "provider_api_version": raw["api"],
        "raw_response_sha256": value_sha256(raw),
        "config_sha256": value_sha256(parser_config),
    }
    artifact["run_status"] = run_status
    if run_status == "completed":
        artifact.pop("error", None)
    return artifact


def make_renderable_document(tmp_path: Path, kind: str) -> SourceDocument:
    path = tmp_path / f"{kind}.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=400)
    if kind == "dominant":
        page.draw_rect(page.rect, color=(0.5, 0.5, 0.5), fill=(0.5, 0.5, 0.5))
        page.insert_text((150, 200), "2", fontsize=6, color=(0.5, 0.5, 0.5))
    elif kind == "native_structure":
        for index, color in enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1))):
            rect = pymupdf.Rect(index * 100, 0, (index + 1) * 100, 400)
            page.draw_rect(rect, color=color, fill=color)
    elif kind == "not_contentless":
        for index, color in enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0))):
            rect = pymupdf.Rect(index * 75, 0, (index + 1) * 75, 400)
            page.draw_rect(rect, color=color, fill=color)
    else:
        raise ValueError(kind)
    pdf.save(path)
    pdf.close()
    return SourceDocument(
        document_id=f"issuer/{kind}",
        issuer="issuer",
        card_name=kind,
        path=path,
        relative_path=path.name,
        sha256=f"{kind}-sha",
        page_count=1,
    )


def test_contentless_detector_accepts_dominant_color_even_with_native_page_number(tmp_path):
    document = make_renderable_document(tmp_path, "dominant")

    evidence = source_page_contentless_evidence(document, 1)

    assert evidence["is_contentless"] is True
    assert evidence["method"] == "dominant_rendered_rgb"
    assert evidence["dominant_rgb_ratio"] >= 0.995
    assert evidence["native_text"] == "2"


def test_contentless_detector_accepts_empty_native_page_with_three_drawings(tmp_path):
    document = make_renderable_document(tmp_path, "native_structure")

    evidence = source_page_contentless_evidence(document, 1)

    assert evidence["is_contentless"] is True
    assert evidence["method"] == "native_empty_no_images_low_drawings"
    assert evidence["native_text"] == ""
    assert evidence["image_count"] == 0
    assert evidence["drawing_count"] == 3


def test_contentless_detector_rejects_non_dominant_page_with_more_than_three_drawings(tmp_path):
    document = make_renderable_document(tmp_path, "not_contentless")

    evidence = source_page_contentless_evidence(document, 1)

    assert evidence["is_contentless"] is False
    assert evidence["method"] == "not_contentless"
    assert evidence["dominant_rgb_ratio"] < 0.995
    assert evidence["drawing_count"] == 4


def test_missing_empty_luna_page_uses_local_contentless_evidence(tmp_path):
    document = make_renderable_document(tmp_path, "dominant")
    raw = valid_response()
    raw["elements"] = []

    validated = validate_provider_response(raw, document, set(), {1})

    assert validated["omitted_blank_pages"] == [1]
    assert validated["pages"][0]["is_blank"] is True
    assert validated["pages"][0]["blank_provenance"]["method"] == "dominant_rendered_rgb"
    assert validated["blank_page_evidence"]["1"]["native_text"] == "2"


def test_contentless_detector_is_not_used_when_luna_markdown_is_nonempty(tmp_path):
    document = make_renderable_document(tmp_path, "dominant")
    raw = valid_response()
    raw["elements"] = []

    with pytest.raises(ValueError, match=r"omits nonblank source pages: \[1\]"):
        validate_provider_response(raw, document, set(), set())


def test_raw_element_coverage_may_omit_only_luna_confirmed_blank_pages(tmp_path):
    document = make_document(tmp_path, 2)
    raw = valid_response(2, {2})

    validated = validate_provider_response(raw, document, {2})

    assert validated["omitted_blank_pages"] == [2]
    assert validated["pages"][1]["is_blank"] is True
    assert validated["pages"][1]["blocks"] == []


def test_raw_element_coverage_rejects_omitted_nonblank_page(tmp_path):
    document = make_document(tmp_path, 2)

    with pytest.raises(ValueError, match=r"omits nonblank source pages: \[2\]"):
        validate_provider_response(valid_response(2, {2}), document, set())


def test_usage_page_count_remains_mandatory_even_when_omitted_page_is_blank(tmp_path):
    document = make_document(tmp_path, 2)
    raw = valid_response(2, {2})
    raw["usage"]["pages"] = 1

    with pytest.raises(ValueError, match="reported 1 pages; expected 2"):
        validate_provider_response(raw, document, {2})


def test_nonblank_page_with_only_meaningless_block_is_rejected(tmp_path):
    document = make_document(tmp_path)
    raw = valid_response()
    raw["elements"][0].pop("coordinates")
    raw["elements"][0]["content"]["markdown"] = ""

    with pytest.raises(ValueError, match=r"lack meaningful layout blocks: \[1\]"):
        validate_provider_response(raw, document, set())


def test_transient_error_retries_and_records_cumulative_attempt_cost(tmp_path, monkeypatch):
    document = prepare_process_test(tmp_path, monkeypatch)
    responses = iter([UpstageHTTPError(429, "rate limited", 0), valid_response()])
    calls = 0

    def request(*_args):
        nonlocal calls
        calls += 1
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(upstage, "request_parse", request)

    result = upstage.process_document(document, "key", 10, 3, False)
    artifact = read_json(upstage.output_path(document))

    assert result["status"] == "completed"
    assert calls == 2
    assert artifact["attempt_count"] == 2
    assert artifact["current_run_attempt_count"] == 2
    assert artifact["cumulative_estimated_cost_usd"] == 0.02
    assert artifact["parser"]["resolved_model"] == "document-parse-260128"
    assert artifact["parser"]["provider_api_version"] == "2.0"
    assert artifact["parser"]["raw_response_sha256"] == value_sha256(valid_response())


@pytest.mark.parametrize(
    "response",
    [UpstageHTTPError(400, "bad request"), {**valid_response(), "elements": []}],
)
def test_permanent_http_or_successful_schema_failure_is_not_retried(tmp_path, monkeypatch, response):
    document = prepare_process_test(tmp_path, monkeypatch)
    calls = 0

    def request(*_args):
        nonlocal calls
        calls += 1
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(upstage, "request_parse", request)

    result = upstage.process_document(document, "key", 10, 3, False)
    artifact = read_json(upstage.output_path(document))

    assert result["status"] == "failed"
    assert calls == 1
    assert artifact["attempt_count"] == 1
    assert artifact["cumulative_estimated_cost_usd"] == 0.01


def test_failed_artifact_attempt_cost_is_accumulated_after_resume(tmp_path, monkeypatch):
    document = prepare_process_test(tmp_path, monkeypatch)

    monkeypatch.setattr(upstage, "request_parse", lambda *_args: (_ for _ in ()).throw(UpstageHTTPError(400, "bad request")))
    first = upstage.process_document(document, "key", 10, 3, False)
    assert first["attempt_count"] == 1

    monkeypatch.setattr(upstage, "request_parse", lambda *_args: valid_response())
    second = upstage.process_document(document, "key", 10, 3, False)
    artifact = read_json(upstage.output_path(document))

    assert second["status"] == "completed"
    assert artifact["attempt_count"] == 2
    assert artifact["current_run_attempt_count"] == 1
    assert artifact["cumulative_estimated_cost_usd"] == 0.02


def test_completed_artifact_requires_raw_provenance_match(tmp_path, monkeypatch):
    document = prepare_process_test(tmp_path, monkeypatch)
    monkeypatch.setattr(upstage, "request_parse", lambda *_args: valid_response())
    upstage.process_document(document, "key", 10, 1, False)
    config_sha256 = value_sha256(upstage.config())

    assert upstage.complete_artifact(upstage.output_path(document), document, config_sha256)

    raw_path = upstage.raw_output_path(document)
    tampered = read_json(raw_path)
    tampered["model"] = "document-parse-tampered"
    raw_path.write_text(json.dumps(tampered), encoding="utf-8")

    assert not upstage.complete_artifact(upstage.output_path(document), document, config_sha256)


def test_failed_raw_is_promoted_locally_when_only_confirmed_blank_page_is_omitted(tmp_path, monkeypatch):
    document = prepare_process_test(tmp_path, monkeypatch, page_count=2, blank_pages={2})
    raw = valid_response(2, {2})
    upstage.write_json(upstage.raw_output_path(document), raw)
    upstage.write_json(upstage.output_path(document), failed_candidate(document, raw))
    calls = 0

    def request(*_args):
        nonlocal calls
        calls += 1
        raise AssertionError("local recovery must not call Upstage")

    monkeypatch.setattr(upstage, "request_parse", request)

    result = upstage.process_document(document, "key", 10, 3, False)
    artifact = read_json(upstage.output_path(document))

    assert result["status"] == "completed"
    assert result["recovered_from_existing_raw"] is True
    assert calls == 0
    assert artifact["run_status"] == "completed"
    assert artifact["current_run_attempt_count"] == 0
    assert artifact["attempt_count"] == 1
    assert artifact["omitted_blank_pages"] == [2]
    assert artifact["recovery"]["external_request_performed"] is False
    assert upstage.complete_artifact(
        upstage.output_path(document),
        document,
        value_sha256(upstage.config()),
    )


def test_failed_raw_with_nonblank_omission_is_not_promoted(tmp_path, monkeypatch):
    document = prepare_process_test(tmp_path, monkeypatch, page_count=2)
    raw = valid_response(2, {2})
    upstage.write_json(upstage.raw_output_path(document), raw)
    upstage.write_json(upstage.output_path(document), failed_candidate(document, raw))
    calls = 0

    def request(*_args):
        nonlocal calls
        calls += 1
        raise UpstageHTTPError(400, "do not retry")

    monkeypatch.setattr(upstage, "request_parse", request)

    result = upstage.process_document(document, "key", 10, 3, False)

    assert result["status"] == "failed"
    assert calls == 1
    assert result["attempt_count"] == 2


def test_failed_raw_recovery_rejects_identity_config_model_and_provenance_mismatch(tmp_path):
    document = make_document(tmp_path, 2)
    raw = valid_response(2, {2})
    parser_config = upstage.config()
    config_sha256 = value_sha256(parser_config)
    candidate = failed_candidate(document, raw)

    variants = []
    wrong_identity = copy.deepcopy(candidate)
    wrong_identity["document_id"] = "issuer/other"
    variants.append(wrong_identity)
    wrong_source = copy.deepcopy(candidate)
    wrong_source["source"]["sha256"] = "wrong"
    variants.append(wrong_source)
    wrong_config = copy.deepcopy(candidate)
    wrong_config["parser"]["config_sha256"] = "wrong"
    variants.append(wrong_config)
    wrong_model = copy.deepcopy(candidate)
    wrong_model["parser"]["resolved_model"] = "document-parse-other"
    variants.append(wrong_model)
    wrong_provenance = copy.deepcopy(candidate)
    wrong_provenance["parser"]["raw_response_sha256"] = "0" * 64
    variants.append(wrong_provenance)

    assert all(
        recoverable_failed_artifact(
            variant,
            raw,
            document,
            parser_config,
            config_sha256,
            {2},
        )
        is None
        for variant in variants
    )


@pytest.mark.parametrize("source_run_status", ["completed", "failed"])
def test_v3_artifact_is_renormalized_to_v4_without_external_request(tmp_path, monkeypatch, source_run_status):
    document = prepare_process_test(tmp_path, monkeypatch)
    raw = valid_response()
    legacy = legacy_candidate(document, raw, source_run_status)
    upstage.write_json(upstage.raw_output_path(document), raw)
    upstage.write_json(upstage.output_path(document), legacy)
    calls = 0

    def request(*_args):
        nonlocal calls
        calls += 1
        raise AssertionError("v3 migration must not call Upstage")

    monkeypatch.setattr(upstage, "request_parse", request)

    result = upstage.process_document(document, "", 10, 3, False, True)
    artifact = read_json(upstage.output_path(document))

    assert result["status"] == "completed"
    assert result["migrated_from_v3"] is True
    assert calls == 0
    assert artifact["parser"]["normalizer_version"] == "full-corpus-v4"
    assert artifact["parser"]["config_sha256"] == value_sha256(upstage.config())
    assert artifact["migration"]["source_run_status"] == source_run_status
    assert artifact["migration"]["external_request_performed"] is False
    assert artifact["current_run_attempt_count"] == 0


def test_v3_failed_contentless_omission_migrates_with_page_evidence(tmp_path):
    document = make_renderable_document(tmp_path, "dominant")
    raw = valid_response()
    raw["elements"] = []
    legacy = legacy_candidate(document, raw, "failed")

    migrated, reason = migrate_v3_artifact(legacy, raw, document, set(), {1})

    assert reason.startswith("migrated")
    assert migrated is not None
    assert migrated["run_status"] == "completed"
    assert migrated["omitted_blank_pages"] == [1]
    assert migrated["blank_page_evidence"]["1"]["method"] == "dominant_rendered_rgb"
    assert migrated["pages"][0]["blank_provenance"] == migrated["blank_page_evidence"]["1"]


def test_v3_migration_strictly_rejects_identity_config_model_api_raw_and_cost_mismatch(tmp_path):
    document = make_document(tmp_path)
    raw = valid_response()
    candidate = legacy_candidate(document, raw)

    variants = []
    wrong_identity = copy.deepcopy(candidate)
    wrong_identity["document_id"] = "issuer/other"
    variants.append(wrong_identity)
    wrong_source = copy.deepcopy(candidate)
    wrong_source["source"]["sha256"] = "wrong"
    variants.append(wrong_source)
    wrong_config = copy.deepcopy(candidate)
    wrong_config["parser"]["config_sha256"] = "wrong"
    variants.append(wrong_config)
    wrong_model = copy.deepcopy(candidate)
    wrong_model["parser"]["resolved_model"] = "document-parse-other"
    variants.append(wrong_model)
    wrong_api = copy.deepcopy(candidate)
    wrong_api["parser"]["provider_api_version"] = "other"
    variants.append(wrong_api)
    wrong_raw = copy.deepcopy(candidate)
    wrong_raw["parser"]["raw_response_sha256"] = "0" * 64
    variants.append(wrong_raw)
    wrong_cost = copy.deepcopy(candidate)
    wrong_cost["cumulative_estimated_cost_usd"] = 999
    variants.append(wrong_cost)

    assert all(
        migrate_v3_artifact(variant, raw, document, set(), set())[0] is None
        for variant in variants
    )


def test_offline_recover_only_preserves_ineligible_artifact_and_never_calls_api(tmp_path, monkeypatch):
    document = prepare_process_test(tmp_path, monkeypatch)
    raw = valid_response()
    raw["elements"] = []
    legacy = legacy_candidate(document, raw, "failed")
    upstage.write_json(upstage.raw_output_path(document), raw)
    upstage.write_json(upstage.output_path(document), legacy)
    before = upstage.output_path(document).read_bytes()
    calls = 0

    def request(*_args):
        nonlocal calls
        calls += 1
        raise AssertionError("offline recovery must never call Upstage")

    monkeypatch.setattr(upstage, "request_parse", request)

    result = upstage.process_document(document, "", 10, 3, False, True)

    assert result["status"] == "offline_recovery_failed"
    assert result["artifact_preserved"] is True
    assert result["external_request_performed"] is False
    assert calls == 0
    assert upstage.output_path(document).read_bytes() == before


def test_offline_recover_only_cli_exits_nonzero_without_loading_key_or_overwriting(tmp_path, monkeypatch):
    document = prepare_process_test(tmp_path, monkeypatch)
    raw = valid_response()
    raw["elements"] = []
    legacy = legacy_candidate(document, raw, "failed")
    upstage.write_json(upstage.raw_output_path(document), raw)
    upstage.write_json(upstage.output_path(document), legacy)
    before = upstage.output_path(document).read_bytes()
    monkeypatch.setattr(upstage, "discover_documents", lambda *_args: [document])
    monkeypatch.setattr(upstage, "exclusive_run_lock", lambda _name: nullcontext())
    monkeypatch.setattr(upstage, "load_env_key", lambda _name: (_ for _ in ()).throw(AssertionError("must not load API key")))
    monkeypatch.setattr(upstage, "request_parse", lambda *_args: (_ for _ in ()).throw(AssertionError("must not call API")))
    monkeypatch.setattr(sys, "argv", ["run_upstage_validation.py", "--offline-recover-only"])

    with pytest.raises(SystemExit, match="offline recovery failed for 1 document"):
        upstage.main()

    assert upstage.output_path(document).read_bytes() == before


def test_resolved_model_validation_rejects_mixed_versions():
    assert validate_resolved_models(["document-parse-260128", "document-parse-260128"]) == "document-parse-260128"
    with pytest.raises(ValueError, match="mixed Upstage resolved models"):
        validate_resolved_models(["document-parse-260128", "document-parse-260501"])
