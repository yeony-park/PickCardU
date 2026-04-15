import os
from collections import Counter
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document

from chroma.easy_ocr import extract_text_from_pdf, extract_text_from_pdf_page
from chroma.quality_utils import (
    detect_review_risks,
    evaluate_text_quality,
    normalize_extracted_text,
)

LOW_QUALITY_PAGE_SCORE = 35.0 # 품질 점수 임계값(35점 미만이면 OCR 재실행)
OCR_IMPROVEMENT_MARGIN = 5.0 # OCR이 내장 텍스트보다 5점 이상 좋아야 대체 

# 폴더 내 모든 PDF 파일 경로 찾기 
def list_pdf_files(folder_path: str) -> list[str]:
    pdf_files = []
    for root, _, files in os.walk(folder_path):
        for filename in files:
            if filename.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, filename))
    return sorted(pdf_files)

# PDF 내장 텍스트 추출 (OCR 사용 안 함)
def extract_native_pdf_pages(filepath: str) -> list[str]:
    loader = PyPDFLoader(filepath)
    pages = loader.load()
    return [normalize_extracted_text(page.page_content) for page in pages]

# 파일 정보 추출
def build_hybrid_document(filepath: str) -> Document | None:
    filename = os.path.basename(filepath)
    company = os.path.basename(os.path.dirname(filepath))
    # 내장 텍스트 추출 시도 
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
                "review_flags": [],
                "review_matches": [],
                "review_count": 0,
            },
        )
    # 페이지별 품질 평가 및 하이브리드 처리 
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

    # 최종 Document 객체 생성 
    final_text = "\n\n".join(selected_pages).strip()
    final_metrics = evaluate_text_quality(final_text)
    review_info = detect_review_risks(final_text)

    if not final_text:
        print(f"[SKIP] 추출 결과 없음: {filename}")
        return None

    doc_type = "hybrid" if ocr_pages_used > 0 else "clean"
    avg_native = round(sum(native_scores) / len(native_scores), 2) if native_scores else 0.0
    avg_ocr = round(sum(ocr_scores) / len(ocr_scores), 2) if ocr_scores else 0.0
    quality_status = (
        "review_needed"
        if final_metrics["score"] < LOW_QUALITY_PAGE_SCORE or review_info["review_needed"]
        else "ready"
    )

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
            "review_flags": review_info["review_flags"],
            "review_matches": review_info["review_matches"],
            "review_count": review_info["review_count"],
        },
    )


# 폴더 내 모든 PDF를 Document 리스트로 로드 
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

    type_counts = Counter(doc.metadata.get("type", "unknown") for doc in all_docs)
    print(f"\n[INFO] 로드된 문서 수: {len(all_docs)}")
    print(f"[INFO] 문서 타입 분포: {dict(type_counts)}")
    return all_docs
