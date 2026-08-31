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

서비스/indexer는 `LexicalSearcher.search`, `VectorSearcher.search` 계약을 구현해
자체 인덱스를 연결합니다. `InMemory*Searcher`는 fixture와 작은 인덱스용입니다. 패키지는
corpus 파일 경로나 snapshot loader를 제공하지 않습니다. `LocalReranker`의 모델 캐시는
수 GB 모델의 중복 로드를 막기 위해 프로세스 수명 동안 유지됩니다.

서비스 런타임의 lexical search는 메모리 BM25가 아니라 active release에 포함된 SQLite
FTS5 `bm25()`를 사용합니다. 검색 프로필은 `card_page_section_benefit`과
`parent_child_bundle`을 명시적으로 구분하며, 하나의 요청에서 서로 다른 프로필의 청크를
섞지 않습니다. 기본 검색은 Vector:FTS5 BM25 `0.4:0.6`, 각 component Top50, reranker
후보 20개, 사용자 출력 Top3입니다. 출력 Top5도 계약상 허용하지만 제품 기본값은 Top3입니다.
section 또는 bundle 부모가 선택되면 답변 직전에 연결된 benefit 자식으로 hydration한 뒤
카드별 근거를 구성합니다. 따라서 상위 청크의 검색 범위는 활용하되, LLM citation은 실제
혜택 leaf의 text와 page provenance를 사용합니다.
