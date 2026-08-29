# Answer-bearing / grouped retrieval reevaluation

개발 질의 30개의 single-run 진단 결과다. holdout은 사용하지 않았고 운영 일반화를 주장하지 않는다. 저장된 16번 후보와 17번 GTE logit만 사용했다.

## 쉬운 용어 풀이

- strict_raw: 기대 카드·level·필수 용어를 모두 만족하는 기존 엄격 기준
- answer_bearing / level_relaxed_term_bearing: 카드와 필수 용어는 맞지만 level을 무시한 진단 기준. 사실적으로 완전한 정답과 같은 뜻이 아니다.
- exact_doc_dedup: 카드와 정규화 문서가 정확히 같은 청크만 하나로 묶는다.
- gold_family_oracle: 모든 answer-bearing 정답을 한 가족으로 보는 평가용 oracle이며 운영 dedup이 아니다.
- Card Hit@3: 상위 3개에 기대 카드가 있는 비율
- Hit@3: 각 view의 관련 단위가 상위 3개에 있는 비율
- Recall@5: 관련 단위 중 상위 5개가 회수한 비율
- MRR@5: 첫 관련 단위가 앞에 있을수록 높은 값
- nDCG@5: 관련 단위를 상위에 둔 정도
- win/loss/tie: 질의별 no-reranker 대비 개선/하락/동률

raw answer-bearing Recall/nDCG는 중복 분모에 민감하므로 Hit/MRR, gold-family Recall/nDCG, exact dedup 순으로 해석한다. 진단은 `answer_chunk_misranking_strengthened`이며 기존 `retain_no_reranker` 판정은 바꾸지 않는다.

## section/benefit-only exploratory follow-up

기존 결과 확인 뒤 추가한 adaptive development diagnostic이다. Evidence 20개만 사용한다. `leaf_only_top20`은 원래 Top50에서 section/benefit을 남긴 뒤 fused rank 앞 20개이며, `leaf_available_from_top50`은 원래 Top50 안에서 필터 후 남은 전체를 쓰는 가변 K다. 후자는 Top50 자체가 아니다.

- available K 범위: 34~46
- 비교: 같은 leaf 후보 집합에서 no-reranker와 저장 GTE logit 순위
- coverage count: `removed_from_top50_count`는 최종 후보에서 빠진 전체, `removed_card_or_page_count`는 level 필터 제거분, `excluded_leaf_tail_count`는 fixed20 뒤 leaf tail이다. available의 leaf tail은 0이다.
- `candidate_count_median`은 짝수 개에서 가운데 두 값 평균을 쓰는 표준 중앙값이다.
- 기존 unfiltered 0.4 Top50 evidence delta: strict MRR -0.1192, answer-bearing MRR -0.0275
- GPU/model/custom code/network/API/embedding/install/Chroma 호출: 0

이 결과가 회복을 보여도 card query 품질, parent context 보존 또는 원래 Top50 밖 recall을 증명하지 않으며 기존 `retain_no_reranker` 판정을 변경하지 않는다.

## Follow-up 3 — gold query-type selective reranker oracle

정답 질의 유형을 미리 아는 사후 oracle 진단이다. 실제 서비스에서 쓸 수 있는 router가 아니며, 새 점수·순위·모델 호출 없이 기존 no-reranker/GTE 행과 Top5를 그대로 선택했다. numeric condition(숫자 조건)과 proper noun(고유명사)은 no-reranker passthrough(기존 순위 통과), semantic(의미형)은 GTE를 선택했다.

- config: 0.4/0.5 × Top20/Top50; primary: 0.4 Top50 evidence20
- paired delta: 같은 질의에서 oracle과 기준 방식의 지표 차이
- WLT: 질의별 win/loss/tie(승/패/동률) 수
- 판정: `worth_testing_deployable_router` (gate 통과: True); 승격이 아니라 배포 가능한 router를 별도 시험할 가치가 있는지에 대한 진단
- 실행: skn25 fresh kernel, CPU/offline; GPU/model/custom code/network/API/new embedding/Chroma/package install 0

개발셋 단일 실행이고 gold label을 사용했으며, 저장 후보와 logit 범위를 벗어나지 않는다. holdout·운영 일반화를 주장하지 않는다.


## 구성

| 항목 | 설명 |
|---|---|
| `candidate_coverage.csv` | 검색 후보 목록 또는 후보 coverage 산출물. |
| `changed_queries.csv` | 평가 또는 검증용 표 형식 산출물. |
| `evaluation_contract.json` | 구조화된 평가 또는 설정 자료. |
| `exact_document_groups.jsonl` | 구조화된 평가 또는 설정 자료. |
| `integrity.json` | 실행 무결성·재현 확인 자료. |
| `leaf_only_candidate_coverage.csv` | 검색 후보 목록 또는 후보 coverage 산출물. |
| `leaf_only_changed_queries.csv` | 평가 또는 검증용 표 형식 산출물. |
| `leaf_only_integrity.json` | 실행 무결성·재현 확인 자료. |
| `leaf_only_paired_deltas.csv` | 동일 질의의 paired 비교 산출물. |
| `leaf_only_per_query_metrics.csv` | 질의·사실별 상세 평가 산출물. |
| `leaf_only_summary.csv` | 집계 요약 산출물. |
| `leaf_only_summary.json` | 집계 요약 산출물. |
| `paired_deltas.csv` | 동일 질의의 paired 비교 산출물. |
| `per_query_metrics.csv` | 질의·사실별 상세 평가 산출물. |
| `relevance_sets.jsonl` | 구조화된 평가 또는 설정 자료. |
| `selective_oracle_contract.json` | 구조화된 평가 또는 설정 자료. |
| `selective_oracle_integrity.json` | 실행 무결성·재현 확인 자료. |
| `selective_oracle_paired_deltas.csv` | 동일 질의의 paired 비교 산출물. |
| `selective_oracle_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `selective_oracle_summary.csv` | 집계 요약 산출물. |
| `selective_oracle_summary.json` | 집계 요약 산출물. |
| `selective_oracle_wlt.csv` | 동일 질의의 paired 비교 산출물. |
| `summary.csv` | 집계 요약 산출물. |
| `summary.json` | 집계 요약 산출물. |
