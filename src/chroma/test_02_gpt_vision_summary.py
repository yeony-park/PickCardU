import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_JSON_DIR = PROJECT_ROOT / "data" / "vision" / "vision_raw_json"
SUMMARY_DIR = PROJECT_ROOT / "data" / "vision" / "category_summary"

MODEL = "gpt-4.1-mini"
client = OpenAI()

CATEGORY_SCHEMA = {
    "type": "json_schema",
    "name": "card_category_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": {
            "type": "array",
            "items": {"type": "string"}
        }
    }
}

def flatten_raw_card_json(card_json: dict) -> str:
    lines = []

    lines.append(f"카드사: {card_json.get('issuer', '')}")
    lines.append(f"카드명: {card_json.get('card_name', '')}")

    for page in card_json.get("pages", []):
        lines.append(f"\n[페이지 {page.get('page_number')}]")

        for fee in page.get("annual_fee", []):
            lines.append(f"연회비: {fee}")

        for benefit in page.get("benefits", []):
            lines.append(
                "혜택: "
                f"카테고리={benefit.get('category', '')}, "
                f"제목={benefit.get('title', '')}, "
                f"설명={benefit.get('description', '')}, "
                f"조건={benefit.get('conditions', [])}, "
                f"한도={benefit.get('limits', [])}"
            )

        for condition in page.get("performance_conditions", []):
            lines.append(f"실적조건: {condition}")

        for exclusion in page.get("exclusions", []):
            lines.append(f"제외항목: {exclusion}")

        for note in page.get("notes", []):
            lines.append(f"유의사항: {note}")

    return "\n".join(lines)

def summarize_categories(card_json: dict) -> dict[str, list[str]]:
    text = flatten_raw_card_json(card_json)

    prompt = f"""
    카드 혜택 정보를 소비 카테고리별 딕셔너리로 요약하세요.

    반환 형태 예시:
    {{
        "교통" : ["요약 문장", "요약 문장"],
        "카페" : ["요약 문장", "요약 문장"],
        "쇼핑" : ["요약 문장", "요약 문장"]
    }}

    규칙:
    - 반드시 JSON 객체만 반환하세요.
    - key는 카테고리명입니다.
    - value는 해당 카테고리 혜택 요약 문장 리스트입니다.
    - 혜택률, 할인율, 적립률, 월 한도, 전월 실적, 제외 조건이 있으면 요약에 포함하세요.
    - 중복 혜택은 합치세요.
    - 카테고리는 사용자가 검색하기 쉬운 한국어 명사로 정리하세요.
    - 근거가 부족하면 임의로 만들지 마세요.

    카드 정보:
    {text}
    """

    response = client.responses.create(
        model = MODEL,
        input = prompt,
        text = {"format": CATEGORY_SCHEMA}
    )

    return json.loads(response.output_text)

def save_json(data: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def run_all() -> None:
    json_paths = sorted(RAW_JSON_DIR.glob("*/*.json"))
    print(f"[INFO] raw JSON 수: {len(json_paths)}")

    for json_path in json_paths:
        issuer = json_path.parent.name
        output_path = SUMMARY_DIR / issuer / json_path.name

        if output_path.exists():
            print(f"[SKIP] 이미 존재: {output_path}")
            continue

        try:
            card_json = json.loads(json_path.read_text(encoding="utf-8"))
            summary = summarize_categories(card_json)
            save_json(summary, output_path)
            print(f"[SAVE] {output_path}")
        except Exception as exc:
            print(f"[ERROR] {json_path}: {exc}")


if __name__ == "__main__":
    run_all()