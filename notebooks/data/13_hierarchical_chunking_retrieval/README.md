# 13. Hierarchical Chunking Retrieval Data

`13_hierarchical_chunking_retrieval.ipynb`의 재현 가능한 입력 manifest, raw-text 청크, embedding cache, 로컬 Chroma index와 retrieval 평가 결과를 보관한다.

- `input_manifest.json`: gold raw TXT와 structured JSON 10쌍의 상대경로·SHA-256 및 coverage 주의사항
- `chunks.jsonl`: card → page → section → benefit 계층의 canonical 청크. `document`는 raw TXT 원문이며 structured 값은 scalar-safe JSON metadata에만 기록한다.
- `embedding_cache/`: `text-embedding-3-small` batch cache. 입력 text hash와 embedding vector만 저장하며 API key는 저장하지 않는다.
- `embedding_usage.json`: 계획/실행 batch, token usage와 cache hit 기록
- `chroma/`: fingerprint 기반 collection을 사용하는 `PersistentClient` 저장소
- `index_manifest.json`: collection 이름, model, input fingerprint와 level별 청크 수
- `retrieval_per_query.csv`: 30개 고정 질의의 keyword/vector/hybrid 상세 결과
- `retrieval_summary.json`, `retrieval_summary.csv`: Hit@3, Recall@5, MRR@5, nDCG@5 요약과 대표 evidence

page와 section은 원문 문단·heading 경계를 우선하며 고정 overlap은 0이다. benefit은 structured label로 가장 관련 있는 raw 문단을 찾고 앞뒤 한 문단을 함께 포함한다. 같은 page에서 여러 label이 동일한 normalized raw 범위를 선택하면 본문은 한 benefit chunk로 병합하고 label metadata는 모두 보존한다. card는 최대 2,000자, page는 6,000자, section은 4,000자, benefit은 3,000자다. card는 전체 본문을 반복하는 parent가 아니라 원문 제목·heading 기반 routing overview이므로 page보다 작다. 긴 page/section은 문단 경계에서만 분할한다.

BC는 selected excerpt, IBK는 incomplete/ambiguous이고 나머지 8개도 시각 감사 전 full-page candidate다. 따라서 이 결과는 해당 canonical fixture 안에서의 retrieval 검증이며 원본 PDF 전체 coverage를 뜻하지 않는다.

노트북의 embedding 셀은 cache miss 시 `LIVE_EMBEDDING_API=true`와 `OPENAI_API_KEY`가 모두 있어야 외부 호출한다. 고정 평가 질의 embedding도 적재 단계에서 함께 cache하므로 이후 retrieval 비교 셀은 offline simulation이다. 실제 서비스 검색은 사용 시점 질의에 대해 별도로 수행한다.


## 구성

| 항목 | 설명 |
|---|---|
| `chroma/` | 직속 하위 자료를 보관하는 폴더다. |
| `chunks.jsonl` | 구조화된 평가 또는 설정 자료. |
| `embedding_usage.json` | 구조화된 평가 또는 설정 자료. |
| `index_manifest.json` | 실행 무결성·재현 확인 자료. |
| `input_manifest.json` | 실행 무결성·재현 확인 자료. |
| `retrieval_ablation_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `retrieval_ablation_summary.csv` | 집계 요약 산출물. |
| `retrieval_ablation_summary.json` | 집계 요약 산출물. |
| `retrieval_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `retrieval_search_normalization_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `retrieval_search_normalization_summary.csv` | 집계 요약 산출물. |
| `retrieval_search_normalization_summary.json` | 집계 요약 산출물. |
| `retrieval_summary.csv` | 집계 요약 산출물. |
| `retrieval_summary.json` | 집계 요약 산출물. |
| `retrieval_true_two_stage_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `retrieval_true_two_stage_summary.csv` | 집계 요약 산출물. |
| `retrieval_true_two_stage_summary.json` | 집계 요약 산출물. |
