import os
import sys
import json
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from chroma.data_loader import load_pdfs_as_documents
from chroma.chunking import chunk_documents

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DATA_PATH = os.path.join(BASE_DIR, "data", "raw")
OCR_TXT_PATH = os.path.join(BASE_DIR, "data", "ocr_output")
CHROMA_DB_PATH = os.path.join(BASE_DIR, "chroma_db")


def sanitize_metadata_for_chroma(docs):
    sanitized_docs = []
    for doc in docs:
        metadata = {}
        for key, value in doc.metadata.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                metadata[key] = value
            elif isinstance(value, list):
                metadata[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, dict):
                metadata[key] = json.dumps(value, ensure_ascii=False)
            else:
                metadata[key] = str(value)

        sanitized_docs.append(
            Document(
                page_content=doc.page_content,
                metadata=metadata,
            )
        )
    return sanitized_docs


def load_ocr_txt_as_documents(folder_path: str):
    docs = []
    if not os.path.exists(folder_path):
        return docs
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(".txt"):
            filepath = os.path.join(folder_path, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if not text:
                continue
            doc = Document(
                page_content=text,
                metadata={
                    "source": filename,
                    "card_name": os.path.splitext(filename)[0],
                    "type": "ocr"
                }
            )
            docs.append(doc)
    print(f"[INFO] OCR txt 문서 수: {len(docs)}")
    return docs


def embed_and_store():
    # 1. 데이터 로드
    clean_docs = load_pdfs_as_documents(RAW_DATA_PATH)
    ocr_docs = load_ocr_txt_as_documents(OCR_TXT_PATH)
    all_docs = clean_docs + ocr_docs
    print(f"[INFO] 전체 문서 수: {len(all_docs)}")

    # 2. 청킹
    chunked_docs = chunk_documents(all_docs)
    chunked_docs = sanitize_metadata_for_chroma(chunked_docs)

    # 3. 임베딩 모델
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=os.getenv("OPENAI_API_KEY")
    )

    # 4. ChromaDB 저장
    vectorstore = Chroma.from_documents(
        documents=chunked_docs,
        embedding=embeddings,
        persist_directory=CHROMA_DB_PATH
    )

    print(f"[INFO] 총 {len(chunked_docs)}개 청크 ChromaDB 저장 완료!")


if __name__ == "__main__":
    embed_and_store()
