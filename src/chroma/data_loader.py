import os
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document


def load_pdfs_as_documents(folder_path: str):
    """
    clean_data 폴더 안의 PDF를 파일 단위 Document로 로드
    """
    all_docs = []

    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"폴더가 존재하지 않습니다: {folder_path}")

    for filename in os.listdir(folder_path):
        if filename.lower().endswith(".pdf"):
            filepath = os.path.join(folder_path, filename)
            print(f"[LOAD] {filename}")

            try:
                loader = PyPDFLoader(filepath)
                pages = loader.load()

                full_text = "\n".join([page.page_content for page in pages]).strip()

                if not full_text:
                    print(f"[SKIP] 내용 비어 있음: {filename}")
                    continue

                doc = Document(
                    page_content=full_text,
                    metadata={
                        "source": filename,
                        "file_path": filepath,
                        "card_name": os.path.splitext(filename)[0],
                        "total_pages": len(pages),
                        "type": "clean"
                    }
                )
                all_docs.append(doc)

            except Exception as e:
                print(f"[ERROR] {filename}: {e}")

    print(f"\n[INFO] clean 문서 수: {len(all_docs)}")
    return all_docs