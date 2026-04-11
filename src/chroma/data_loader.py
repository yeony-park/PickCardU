import os
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document

from easy_ocr import extract_text_from_pdf, extract_text_from_pdf_page
from quality_utils import evaluate_text_quality, normalize_extracted_text

LOW_QUALITY_PAGE_SCORE = 35.0
OCR_IMPROVEMENT_MARGIN = 5.0


def list_pdf_files(folder_path: str) -> list[str]:
    pdf_files = []
    for root, _, files in os.walk(folder_path):
        for filename in files:
            if filename.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, filename))
    return sorted(pdf_files)


def extract_native_pdf_pages(filepath: str) -> list[str]:
    loader = PyPDFLoader(filepath)
    pages = loader.load()
    return [normalize_extracted_text(page.page_content) for page in pages]


def build_hybrid_document(filepath: str) -> Document | None:
    filename = os.path.basename(filepath)
    company = os.path.basename(os.path.dirname(filepath))

    try:
        native_pages = extract_native_pdf_pages(filepath)
    except Exception as native_error:
        print(f"[WARN] native 추출 실패, 전체 OCR로 전환: {filename} ({native_error})")
        ocr_result = extract_text_from_pdf(filepath)
        if not ocr_result["text"]:
            print(f"[SKIP] OCR도 실패: {filename}")
            return None

        return Document(
            page_content=ocr_result["text"],
            metadata={
                "source": filename,
                "file_path": filepath,
                "card_name": os.path.splitext(filename)[0],
                "total_pages": len(ocr_result["pages"]),
                "type": "ocr",
                "card_company": company,
                "quality_score": ocr_result["score"],
                "native_quality_score": 0.0,
                "ocr_quality_score": ocr_result["score"],
                "ocr_pages": len(ocr_result["pages"]),
                "low_quality_pages": len(ocr_result["pages"]),
                "quality_status": "ocr_only",
            },
        )

    if not native_pages:
        print(f"[SKIP] 페이지 없음: {filename}")
        return None

    selected_pages = []
    native_scores = []
    ocr_scores = []
    ocr_pages_used = 0
    low_quality_pages = 0

    for page_index, native_text in enumerate(native_pages, start=1):
        native_metrics = evaluate_text_quality(native_text)
        native_scores.append(native_metrics["score"])

        selected_text = native_text
        selected_method = "native"

        if native_metrics["score"] < LOW_QUALITY_PAGE_SCORE:
            low_quality_pages += 1
            try:
                ocr_result = extract_text_from_pdf_page(filepath, page_index)
            except Exception as ocr_error:
                print(f"[WARN] OCR 실패 {filename} page {page_index}: {ocr_error}")
                ocr_result = {"text": "", "score": 0.0}

            ocr_scores.append(ocr_result["score"])
            if ocr_result["score"] > native_metrics["score"] + OCR_IMPROVEMENT_MARGIN:
                selected_text = ocr_result["text"]
                selected_method = "ocr"
                ocr_pages_used += 1
        if selected_text:
            selected_pages.append(f"[page {page_index}][{selected_method}]\n{selected_text}")

    final_text = "\n\n".join(selected_pages).strip()
    final_metrics = evaluate_text_quality(final_text)

    if not final_text:
        print(f"[SKIP] 추출 결과 없음: {filename}")
        return None

    doc_type = "hybrid" if ocr_pages_used > 0 else "clean"
    avg_native = round(sum(native_scores) / len(native_scores), 2) if native_scores else 0.0
    avg_ocr = round(sum(ocr_scores) / len(ocr_scores), 2) if ocr_scores else 0.0
    quality_status = "review_needed" if final_metrics["score"] < LOW_QUALITY_PAGE_SCORE else "ready"

    return Document(
        page_content=final_text,
        metadata={
            "source": filename,
            "file_path": filepath,
            "card_name": os.path.splitext(filename)[0],
            "total_pages": len(native_pages),
            "type": doc_type,
            "card_company": company,
            "quality_score": final_metrics["score"],
            "native_quality_score": avg_native,
            "ocr_quality_score": avg_ocr,
            "ocr_pages": ocr_pages_used,
            "low_quality_pages": low_quality_pages,
            "quality_status": quality_status,
        },
    )


def load_pdfs_as_documents(folder_path: str):
    """
    raw 폴더 안의 PDF를 품질 점수 기반 하이브리드 방식으로 로드
    """
    all_docs = []

    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"폴더가 존재하지 않습니다: {folder_path}")

    for filepath in list_pdf_files(folder_path):
        filename = os.path.basename(filepath)
        print(f"[LOAD] {filename}")
        try:
            doc = build_hybrid_document(filepath)
            if doc is not None:
                all_docs.append(doc)
        except Exception as e:
            print(f"[ERROR] {filename}: {e}")

    print(f"\n[INFO] hybrid 문서 수: {len(all_docs)}")
    return all_docs
