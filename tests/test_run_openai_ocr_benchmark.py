import ssl
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ocr_benchmark"
sys.path.insert(0, str(SCRIPT_DIR))

from run_openai_ocr_benchmark import (
    CONDITIONS,
    api_ssl_context,
    api_payload,
    document_prompt,
    extract_api_output_text,
    extract_cli_usage,
    normalize_page_output,
)


def test_requested_conditions_are_exact():
    assert [(item.surface, item.model, item.reasoning, item.detail) for item in CONDITIONS] == [
        ("cli", "gpt-5.6-luna", "max", None),
        ("cli", "gpt-5.6-terra", "medium", None),
        ("cli", "gpt-5.6-terra", "high", None),
        ("cli", "gpt-5.6-sol", "medium", None),
        ("cli", "gpt-5.6-sol", "high", None),
        ("api", "gpt-5.6-luna", "max", "high"),
    ]


def test_normalize_page_output_strips_fence_and_forces_page_number():
    raw = '```json\n{"page_num": 99, "status": "success", "markdown": "연회비 5,000원", "uncertain_spans": []}\n```'

    result = normalize_page_output(raw, page_num=2)

    assert result == {
        "page_num": 2,
        "status": "success",
        "markdown": "연회비 5,000원",
        "uncertain_spans": [],
    }


def test_extract_cli_usage_sums_token_count_events():
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 30},
        },
    ]

    assert extract_cli_usage(events) == {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 30,
        "reasoning_tokens": None,
        "total_tokens": 130,
    }


def test_extract_api_output_text_reads_all_output_text_blocks():
    response = {
        "output": [
            {"type": "reasoning"},
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "{\"page_num\":"},
                    {"type": "output_text", "text": "1}"},
                ],
            },
        ]
    }

    assert extract_api_output_text(response) == '{"page_num":\n1}'


def test_api_payload_sends_pdf_directly_with_high_detail(tmp_path):
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-test")

    payload = api_payload(CONDITIONS[-1], pdf_path, "prompt")
    content = payload["input"][0]["content"]

    assert content[0] == {
        "type": "input_file",
        "filename": "document.pdf",
        "file_data": "data:application/pdf;base64,JVBERi10ZXN0",
        "detail": "high",
    }
    assert content[1] == {"type": "input_text", "text": "prompt"}
    assert not any(item["type"] == "input_image" for item in content)


def test_api_ssl_context_keeps_certificate_verification_enabled():
    context = api_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.get_ca_certs()


def test_document_prompt_requires_every_pdf_page_including_covers():
    prompt = document_prompt(page_count=6, input_kind="pdf")

    assert "표지, 뒷표지, 로고만 있는 페이지, 빈 페이지" in prompt
    assert "page_num 1부터 6까지 순서대로 정확히 6개 객체" in prompt
