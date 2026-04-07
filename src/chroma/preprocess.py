import os
from data_loader import load_pdfs_as_documents
from chunking import chunk_documents
from easy_ocr import load_ocr_txt_as_documents

# __file__ 기준 절대경로 (실행 위치와 무관하게 동작)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN_DATA_PATH = os.path.join(BASE_DIR, "data", "clean_data")
OCR_TXT_PATH = os.path.join(BASE_DIR, "data", "ocr_output")


def print_sample_chunks(chunked_docs, n=3):
    print("\n" + "=" * 100)
    print("[SAMPLE CHUNKS]")
    print("=" * 100)

    for i, doc in enumerate(chunked_docs[:n], 1):
        print(f"\n[Chunk {i}]")
        print("카드명:", doc.metadata.get("card_name"))
        print("타입:", doc.metadata.get("type"))
        print("원본 파일:", doc.metadata.get("source"))
        print("-" * 80)
        print(doc.page_content[:500])


def main():
    # 1. clean PDF 로드
    clean_docs = load_pdfs_as_documents(CLEAN_DATA_PATH)

    # 2. OCR txt 로드
    ocr_docs = load_ocr_txt_as_documents(OCR_TXT_PATH)

    # 3. 문서 합치기
    all_docs = clean_docs + ocr_docs
    print(f"\n[INFO] 전체 문서 수: {len(all_docs)}")

    # 4. 청킹
    chunked_docs = chunk_documents(all_docs, chunk_size=800, chunk_overlap=120)

    # 5. 샘플 출력
    print_sample_chunks(chunked_docs, n=5)


if __name__ == "__main__":
    main()
