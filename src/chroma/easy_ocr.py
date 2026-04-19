import os
import easyocr
import numpy as np
import re
from pdf2image import convert_from_path
from PIL import ImageOps, ImageFilter
from langchain_core.documents import Document
from chroma.quality_utils import evaluate_text_quality, normalize_extracted_text

# OCR 엔진 인스턴스 
# 한국어 ko + 영어 en
reader = easyocr.Reader(['ko', 'en'],  gpu=True, verbose=False)

# 텍스트의 공백 처리 -> 각 줄의 앞뒤 공백 제거 
def normalize_whitespace(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)

# OCR 오인식 단어 자동 수정
def fix_common_ocr_words(text: str) -> str:
    corrections = {
        "금음": "금융",
        "금웅": "금융",
        "발굽": "발급",
        "수수로": "수수료",
        "흉페이지": "홈페이지",
        "가행점": "가맹점",
        "가맣점": "가맹점",
        "가멍점": "가맹점",
        "라이프스타일올": "라이프스타일을",
        "금움": "금융",
        "부가서비스트": "부가서비스",
        "수악성": "수익성",
        "유호기간": "유효기간",
        "영향울": "영향을",
        "카드틀": "카드를",
        "되니다": "됩니다",
        "활수": "할 수",
        "실식": "실적",
        "약정이울": "약정이율",
        "가중평군대출금리": "가중평균대출금리",
        "금중소비자": "금융소비자",
    }

    for wrong, right in corrections.items():
        text = text.replace(wrong, right)

    return text

# OCR이 "96"를 인식한 "%" 기호 수정 
# keyword가 있을 때만 처리 
def fix_percent_ocr_safe(text: str) -> str:
    keywords = ["할인", "수수료", "금리", "이자", "적립", "이용수수료"]
    fixed_lines = []

    for line in text.splitlines():
        if any(keyword in line for keyword in keywords):
            def replacer(match):
                original = match.group(0)
                num_str = match.group(1)

                try:
                    num = float(num_str)
                    if 0 <= num <= 100:
                        return f"{num_str}%"
                    return original
                except ValueError:
                    return original

            line = re.sub(r'(?<!\d)(\d+(?:\.\d+)?)96(?!\d)', replacer, line)

        fixed_lines.append(line)

    return "\n".join(fixed_lines)

# OCR 후 텍스트 정제 파이프라인 
def postprocess_ocr_text(text: str) -> str:
    text = normalize_whitespace(text)
    text = fix_common_ocr_words(text)
    text = fix_percent_ocr_safe(text)
    return normalize_extracted_text(text)

# OCR 전 이미지 전처리 
def preprocess_image_for_ocr(image):
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = gray.filter(ImageFilter.MedianFilter(size=3))
    return gray

# 이미지 한 장에서 텍스트 추출
def extract_text_from_image(image) -> str:
    processed = preprocess_image_for_ocr(image)
    image_np = np.array(processed)
    result = reader.readtext(image_np, detail=0)
    if not result:
        return ""
    return postprocess_ocr_text(" ".join(result).strip())

# PDF 한 페이지만 OCR
def extract_text_from_pdf_page(file_path: str, page_number: int, dpi: int = 300) -> dict:
    images = convert_from_path(
        file_path,
        dpi=dpi,
        first_page=page_number,
        last_page=page_number,
    )
    if not images:
        return {
            "text": "",
            "score": 0.0,
            "page_number": page_number,
        }

    text = extract_text_from_image(images[0])
    metrics = evaluate_text_quality(text)
    return {
        "text": text,
        "score": metrics["score"],
        "page_number": page_number,
        "metrics": metrics,
    }

# PDF 전체 OCR
def extract_text_from_pdf(file_path: str, dpi: int = 300) -> dict:
    images = convert_from_path(file_path, dpi=dpi)
    page_results = []
    full_text = []

    for index, image in enumerate(images, start=1):
        text = extract_text_from_image(image)
        metrics = evaluate_text_quality(text)
        if text:
            full_text.append(f"[page {index}]\n{text}")
        page_results.append(
            {
                "page_number": index,
                "text": text,
                "score": metrics["score"],
                "metrics": metrics,
            }
        )

    combined_text = "\n\n".join(full_text).strip()
    overall_metrics = evaluate_text_quality(combined_text)
    return {
        "text": combined_text,
        "score": overall_metrics["score"],
        "metrics": overall_metrics,
        "pages": page_results,
    }

# PDF OCR -> TXT 파일로 저장
def ocr_pdf_and_save_txt(file_path: str, output_folder: str):
    """
    PDF 1개를 OCR + 후처리 후 바로 txt 저장
    """
    os.makedirs(output_folder, exist_ok=True)

    filename = os.path.basename(file_path)
    save_name = filename.replace(".pdf", ".txt")
    save_path = os.path.join(output_folder, save_name)

    try:
        result = extract_text_from_pdf(file_path)
    except Exception as e:
        print(f"[ERROR] {file_path}: {e}")
        return

    final_text = result["text"].strip()

    if not final_text:
        print(f"[SKIP] 텍스트 없음: {filename}")
        return

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(final_text)

    print(f"[SAVE] {save_name}")

# 폴더 내 모든 PDF를 OCR 후 TXT 저장
def save_ocr_pdfs_to_txt(pdf_folder: str, output_folder: str):
    """
    폴더 내 PDF들을 OCR 후 바로 txt 저장
    """
    if not os.path.exists(pdf_folder):
        raise FileNotFoundError(f"폴더가 존재하지 않습니다: {pdf_folder}")

    for filename in os.listdir(pdf_folder):
        if filename.lower().endswith(".pdf"):
            file_path = os.path.join(pdf_folder, filename)
            print(f"[OCR] {filename}")
            ocr_pdf_and_save_txt(file_path, output_folder)

# OCR 결과 TXT 파일을 LangChain Document로 로드 
def load_ocr_txt_as_documents(folder_path: str, card_company: str = None):
    docs = []

    if not os.path.exists(folder_path):
        return docs

    for filename in os.listdir(folder_path):
        if filename.lower().endswith(".txt"):
            filepath = os.path.join(folder_path, filename)

            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read().strip()

            if not text:
                print(f"[SKIP] 빈 txt: {filename}")
                continue

            doc = Document(
                page_content=text,
                metadata={
                    "source": filename,
                    "file_path": filepath,
                    "card_name": os.path.splitext(filename)[0],
                    "type": "ocr",
                    "card_company": card_company if card_company else "unknown"
                }
            )
            docs.append(doc)

    print(f"[INFO] OCR txt 문서 수: {len(docs)}")
    return docs
