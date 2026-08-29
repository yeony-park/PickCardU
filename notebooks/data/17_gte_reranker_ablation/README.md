# GTE reranker ablation

개발 질의 30개에서 저장된 RRF 후보를 GTE logit으로만 재정렬한 결과다. holdout은 사용하지 않았고 운영 일반화를 주장하지 않는다.

## 지표 풀이

- Card Hit@3: 상위 3개에 기대 카드가 있는 비율
- Evidence Strict Hit@3: 상위 3개에 엄격 관련 근거가 있는 비율
- Recall@5: 관련 근거 중 상위 5개가 찾은 비율
- MRR@5: 첫 관련 근거가 앞에 있을수록 높은 값
- nDCG@5: 관련 근거의 상위 순위 배치 품질
- candidate ceiling: 현재 후보 집합 안에서 가능한 관련 근거 회수 상한
- win/loss/tie: 질의별 baseline 대비 개선/하락/동률

## 재현 계약

- 모델 `8215cf04918ba6f7b6a62bb44238ce2953d8831c`, custom code `40ced75c3017eb27626c9d4ea981bde21a2662f4`의 로컬 cache만 사용
- `CUDA_VISIBLE_DEVICES=0`, physical GPU 0, dtype float16, batch 2
- 원본 query + chunk.document만 입력, max_length 8192, only_second truncation
- network/API/new embedding/package install 0, HNSW query 0
- 품질용 unique pair 1857개를 한 번씩만 점수화

선택: `retain_no_reranker` / `vector_0.5_bm25_0.5_no_reranker`. Raw metric 최고 reranker 조합은 `vector_0.4_bm25_0.6_top50`다.


## 구성

| 항목 | 설명 |
|---|---|
| `gte_reranker_candidate_ceiling.csv` | 검색 후보 목록 또는 후보 coverage 산출물. |
| `gte_reranker_pair_scores.csv` | 평가 또는 검증용 표 형식 산출물. |
| `gte_reranker_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `gte_reranker_resources.json` | 구조화된 평가 또는 설정 자료. |
| `gte_reranker_run_manifest.json` | 실행 무결성·재현 확인 자료. |
| `gte_reranker_summary.csv` | 집계 요약 산출물. |
| `gte_reranker_summary.json` | 집계 요약 산출물. |
