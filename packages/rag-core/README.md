# RAG Core

저장소, HTTP, 인증과 분리된 PickCardU 검색·재정렬·근거 답변 라이브러리입니다. UI는 이
패키지를 직접 import하지 않고 service API를 사용합니다.

```python
from pickcardu_rag import (
    Chunk, InMemoryBM25Searcher, InMemorySquaredL2Searcher,
    RagPipeline, SearchConfig,
)

chunks = [Chunk("c1:1", "카페 할인", "c1", "카드1", "발급사", "benefit", 1)]
lexical = InMemoryBM25Searcher(chunks)
vector = InMemorySquaredL2Searcher(["c1:1"], embeddings, embedding_model="text-embedding-3-small")
pipeline = RagPipeline(chunks, lexical, vector)
result = pipeline.search(query, query_embedding, SearchConfig(reranker="off"))
```

서비스/indexer는 `LexicalSearcher.search`, `VectorSearcher.search/vector` 계약을 구현해
자체 인덱스를 연결합니다. `InMemory*Searcher`는 fixture와 작은 인덱스용입니다. 패키지는
corpus 파일 경로나 snapshot loader를 제공하지 않습니다. `LocalReranker`의 모델 캐시는
수 GB 모델의 중복 로드를 막기 위해 프로세스 수명 동안 유지됩니다.
