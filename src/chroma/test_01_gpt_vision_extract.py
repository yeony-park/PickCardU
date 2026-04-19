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
            "card_name": {"type": "string"},
            "issuer": {"type": "string"},
            "page_number": {"type": "integer"},
            "annual_fee": {
                "type": "array",
                "items": {"type": "string"}
            },
            "benefits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "category": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "conditions": {"type": "array", "items": {"type": "string"}},
                        "limits": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["category", "title", "description", "conditions", "limits"]
                }
            },
            "performance_conditions": {"type":"array", "items": {"type":"string"}},
            "exclusions": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
            "raw_text": {"type": "string"}
        },
        "required": [
            "card_name",
            "issuer",
            "page_number",
            "annual_fee",
            "benefits",
            "performance_conditions",
            "exclusions",
            "notes",
            "raw_text"
        ]
    }
}

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

    목표:
    - 화면에 보이는 모든 혜택, 조건, 제외 항목, 실적 조건, 유의사항을 빠짐없이 추출하세요.
    - 표 형태 내용도 행 단위 의미가 사라지지 않도록 풀어서 적으세요.
    - 확실하지 않은 내용은 추측하지 말고 raw_text에 보이는 그대로 적으세요.
    - 반드시 JSON으로만 응답하세요.

    파일명: {pdf_path.name}
    페이지 번호 : {page_number}
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
    run_all()