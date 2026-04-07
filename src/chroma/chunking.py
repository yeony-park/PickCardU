from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_documents(docs, chunk_size=800, chunk_overlap=120):
    """
    Document 리스트를 chunk 단위로 분할
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ".", " ", ""]
    )

    chunked_docs = splitter.split_documents(docs)
    print(f"[INFO] chunk 수: {len(chunked_docs)}")
    return chunked_docs