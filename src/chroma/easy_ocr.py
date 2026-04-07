import os
import easyocr
import numpy as np
import re
from pdf2image import convert_from_path
from langchain_core.documents import Document

reader = easyocr.Reader(['ko', 'en'], gpu=False, verbose=False)

def normalize_whitespace(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)

def fix_common_ocr_words(text: str) -> str:
    corrections = {
        "금음": "금융",
        "발굽": "발급",
        "수수로": "수수료",
        "흉페이지": "홈페이지",
        "가행점": "가맹점",
        "라이프스타일올": "라이프스타일을",
        "금움": "금융",
        "부가서비스트": "부가서비스",
        "수악성": "수익성",
    }

    for wrong, right in corrections.items():
        text = text.replace(wrong, right)

    return text

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


def postprocess_ocr_text(text: str) -> str:
    text = normalize_whitespace(text)
    text = fix_common_ocr_words(text)
    text = fix_percent_ocr_safe(text)
    return text


def ocr_pdf_and_save_txt(file_path: str, output_folder: str):
    """
    PDF 1개를 OCR + 후처리 후 바로 txt 저장
    """
    os.makedirs(output_folder, exist_ok=True)

    filename = os.path.basename(file_path)
    save_name = filename.replace(".pdf", ".txt")
    save_path = os.path.join(output_folder, save_name)

    images = convert_from_path(file_path)
    full_text = []

    for i, image in enumerate(images):
        try:
            image_np = np.array(image)
            result = reader.readtext(image_np, detail=0)

            if not result:
                print(f"[WARN] 페이지 {i+1} OCR 결과 없음: {file_path}")
                continue

            text = " ".join(result).strip()
            text = postprocess_ocr_text(text)

            if text:
                full_text.append(text)

        except Exception as e:
            print(f"[ERROR] {file_path} - page {i+1}: {e}")

    final_text = "\n".join(full_text).strip()

    if not final_text:
        print(f"[SKIP] 텍스트 없음: {filename}")
        return

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(final_text)

    print(f"[SAVE] {save_name}")


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


def load_ocr_txt_as_documents(folder_path: str, card_company: str = None):
    docs = []

    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"폴더가 존재하지 않습니다: {folder_path}")

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