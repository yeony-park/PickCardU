import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pdf2image import convert_from_path

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PDF_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "data" / "vision" / "vision_raw_json"

MODEL = "gpt-4.1-mini"

client = OpenAI()

PAGE_SCHEMA = {
    "type": "json_schema",
    "name": "card_pdf_page_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "card_name":   {"type": "string"},
            "issuer":      {"type": "string"},
            "page_number": {"type": "integer"},

            # common structured field 1: contact information (phone, website, overseas phone)
            "contact": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label":   {"type": "string"},
                        "phone":   {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "website": {"anyOf": [{"type": "string"}, {"type": "null"}]}
                    },
                    "required": ["label", "phone", "website"]
                }
            },

            # common structured field 2: annual fee table (empty array if not available)
            "annual_fee": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "card_holder":    {"type": "string"},  # 본인 / 가족
                        "brand":          {"type": "string"},  # BC / MasterCard / VISA
                        "scope":          {"type": "string"},  # 국내전용 / 해외겸용
                        "total_fee":      {"type": "string"},
                        "base_fee":       {"type": "string"},
                        "affiliate_fee":  {"type": "string"}
                    },
                    "required": ["card_holder", "brand", "scope", "total_fee", "base_fee", "affiliate_fee"]
                }
            },

            # 나머지 모든 섹션을 블록 단위로 반환
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        # provide prompt-based block classification based on visual layout and content cues
                        "block_type": {"type": "string"},
                        "title":      {"type": "string"},

                        "items": {
                            "type": "array",
                            "items": {"type": "string"}
                        },

                        "table": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "headers": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "rows": {
                                    "type": "array",
                                    "items": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    }
                                }
                            },
                            "required": ["headers", "rows"]
                        },

                        # raw text of the block for reference (do not attempt to parse further)
                        "raw_text": {"type": "string"}
                    },
                    "required": ["block_type", "title", "items", "table", "raw_text"]
                }
            }
        },
        "required": ["card_name", "issuer", "page_number", "contact", "annual_fee", "blocks"]
    }
}

BLOCK_TYPES = """
- annual_fee              : 연회비 안내 (금액 테이블)
- annual_fee_refund_policy: 연회비 반환 조건
- family_card_policy      : 가족카드 이용 안내
- info_overseas           : 해외 이용 안내 (수수료 계산 등)
- benefit_discount        : 청구할인 혜택
- benefit_conditions      : 서비스 제공 조건 / 전월 실적 기준
- installment_service     : 무이자할부 서비스
- brand_service           : 국제 브랜드 서비스
- policy_addon_service    : 부가서비스 유지/변경 정책
- general_notice          : 기타/일반 안내
"""

def image_to_data_url(image_path: Path) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"

def pdf_to_page_images(pdf_path: Path, temp_dir: Path) -> list[Path]:
    temp_dir.mkdir(parents=True, exist_ok=True)

    pages = convert_from_path(
        pdf_path,
        dpi=180,
        fmt="png"
    )

    image_paths = []
    for idx, page in enumerate(pages, start=1):
        image_path = temp_dir / f"{pdf_path.stem}_page_{idx:03d}.png"
        page.save(image_path, "PNG")
        image_paths.append(image_path)

    return image_paths

def extract_page_json(pdf_path: Path, image_path: Path, page_number: int) -> dict:
    prompt = f"""
이 이미지는 카드 상품 설명서 PDF의 한 페이지입니다.

추출 규칙:
1. 페이지에 보이는 모든 섹션을 blocks 배열에 하나씩 담으세요.
2. block_type은 반드시 아래 목록 중 하나만 사용하세요:
{BLOCK_TYPES}
3. 테이블(표) 형태의 내용은 table.headers / table.rows 구조로 정확히 분리하세요.
   예) 할인 한도 표 → headers: ["전월 실적", "매일 할인 한도", ...], rows: [["15만원 이상", "2,500원", ...], ...]
4. 불릿/조건 나열은 items 배열에 항목별로 분리하세요.
5. raw_text에는 해당 섹션 원문 전체를 그대로 담으세요.
6. 연락처는 contact 배열에 담되, label은 번호의 용도를 구체적으로 기술하세요.
   예) "일반 고객센터" X → "카드분실/일반문의 고객센터" O
   예) "비씨카드 고객센터" X → "해외원화결제(DCC) 차단 신청 고객센터" O
   연락처가 없는 페이지는 빈 배열 []로 반환하세요.
7. 연회비 표가 보이는 경우에만 annual_fee 배열에 행 단위로 구조화하세요.
   연회비 표가 없는 페이지는 반드시 빈 배열 []을 반환하세요.
8. 확실하지 않은 내용은 추측하지 말고 raw_text에만 기록하세요.

파일명: {pdf_path.name}
페이지 번호: {page_number}
"""

    response = client.responses.create(
        model = MODEL,
        input = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text":prompt},
                    {
                        "type": "input_image",
                        "image_url": image_to_data_url(image_path),
                        "detail": "high"
                    }
                ]
            }
        ],
        text={"format": PAGE_SCHEMA}
    )

    return json.loads(response.output_text)

def extract_pdf(pdf_path: Path) -> dict:
    issuer = pdf_path.parent.name
    card_name = pdf_path.stem

    temp_dir = OUTPUT_DIR / "_tmp" / issuer / card_name
    page_images = pdf_to_page_images(pdf_path, temp_dir)

    pages = []
    for page_number, image_path in enumerate(page_images, start=1):
        print(f"[VISION] {issuer}/{pdf_path.name} page {page_number}")
        page_json = extract_page_json(pdf_path, image_path, page_number)
        pages.append(page_json)

    return {
        "issuer": issuer,
        "card_name": card_name,
        "source_pdf": str(pdf_path),
        "page_count": len(pages),
        "pages": pages
    }

def save_json(data: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def run_all() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pdf_paths = sorted(RAW_PDF_DIR.glob("*/*.pdf"))
    print(f"[INFO] PDF : {len(pdf_paths)}")

    for pdf_path in pdf_paths:
        issuer = pdf_path.parent.name
        output_path = OUTPUT_DIR / issuer / f"{pdf_path.stem}.json"

        if output_path.exists():
            print(f"[SKIP] already exist: {output_path}")
            continue
        
        try:
            result = extract_pdf(pdf_path)
            save_json(result, output_path)
            print(f"[SAVE] {output_path}")
        except Exception as e:
            print(f"[ERROR] {pdf_path}: {e}")

if __name__ == "__main__":
    pdf_path = RAW_PDF_DIR / "BC" / "BC_Baro_Clear_Plus.pdf"
    
    issuer = pdf_path.parent.name
    output_path = OUTPUT_DIR / issuer / f"{pdf_path.stem}.json"
    
    result = extract_pdf(pdf_path)
    save_json(result, output_path)
    print(f"[SAVE] {output_path}")
    # run_all()