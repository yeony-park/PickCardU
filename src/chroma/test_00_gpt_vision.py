import base64
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pdf2image import convert_from_path

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PDF_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "data" / "vision" / "vision_raw_text"

MODEL = "gpt-4.1-mini"
# MODEL = "gpt-5-mini-2025-08-07"
# MODEL = "gpt-5.4-nano-2026-03-17"

client = OpenAI()


def image_to_data_url(image_path: Path) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def pdf_to_page_images(pdf_path: Path, temp_dir: Path) -> list[Path]:
    temp_dir.mkdir(parents=True, exist_ok=True)

    pages = convert_from_path(
        pdf_path,
        dpi=300,
        fmt="png"
    )

    image_paths = []
    for idx, page in enumerate(pages, start=1):
        image_path = temp_dir / f"{pdf_path.stem}_page_{idx:03d}.png"
        page.save(image_path, "PNG")
        image_paths.append(image_path)

    return image_paths


def extract_page_text(pdf_path: Path, image_path: Path, page_number: int) -> str:
    prompt = f"""
    이 이미지는 카드 상품 설명서 PDF의 한 페이지입니다.
    페이지에 보이는 모든 텍스트를 원문 그대로 전사하세요.

    규칙:
    1. 해석, 요약, 재구성 금지. 보이는 문자만 그대로 옮기세요.
    2. 다단(멀티컬럼) 레이아웃의 경우, 각 단을 위→아래로 읽은 뒤 왼쪽 단부터 순서대로 출력하세요.
    3. 섹션 제목, 본문, 불릿(·, -, ※), 번호(①②③), 표, 각주, 하단 안내문 모두 포함하세요.
    4. 표(테이블)는 마크다운 표 형식으로 출력하세요.
    예)
    | 구분 | 브랜드 | 총연회비 |
    |------|--------|----------|
    | 국내전용 | BC | 5천원 |
    5. ※, ·, ①②③ 등 특수기호는 원문 그대로 보존하세요.
    6. 불명확한 글자는 추측하지 말고 [?]로 표시하세요.
    7. 마크다운 기호(#, **, ``` 등)를 임의로 추가하지 마세요.
    8. 파일명, 페이지 번호 안내 문구는 출력하지 마세요. 추출된 텍스트만 출력하세요.

    파일명: {pdf_path.name}
    페이지 번호: {page_number}
    """

    response = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": image_to_data_url(image_path),
                        "detail": "high"
                    }
                ]
            }
        ]
    )

    return response.output_text.strip()


def extract_pdf_text(pdf_path: Path) -> str:
    issuer = pdf_path.parent.name
    card_name = pdf_path.stem

    temp_dir = OUTPUT_DIR / "_tmp" / issuer / card_name
    page_images = pdf_to_page_images(pdf_path, temp_dir)

    page_texts = []
    for page_number, image_path in enumerate(page_images, start=1):
        print(f"[VISION] {issuer}/{pdf_path.name} page {page_number}")
        page_text = extract_page_text(pdf_path, image_path, page_number)

        page_texts.append(
            f"[PAGE {page_number}]\n{page_text}"
        )

    return "\n\n" + ("\n\n" + ("-" * 80) + "\n\n").join(page_texts) + "\n"


def save_text(text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


def run_all() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pdf_paths = sorted(RAW_PDF_DIR.glob("*/*.pdf"))
    print(f"[INFO] PDF : {len(pdf_paths)}")

    for pdf_path in pdf_paths:
        issuer = pdf_path.parent.name
        output_path = OUTPUT_DIR / issuer / f"{pdf_path.stem}.txt"

        if output_path.exists():
            print(f"[SKIP] already exist: {output_path}")
            continue

        try:
            result_text = extract_pdf_text(pdf_path)
            save_text(result_text, output_path)
            print(f"[SAVE] {output_path}")
        except Exception as e:
            print(f"[ERROR] {pdf_path}: {e}")


if __name__ == "__main__":
    run_all()