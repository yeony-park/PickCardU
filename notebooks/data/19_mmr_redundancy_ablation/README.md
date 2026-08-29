# 19 MMR redundancy ablation

Codex coder agent가 `skn25` fresh kernel, CPU/offline으로 실행한 개발셋 단일 진단이다. 저장된 0.4:0.6 RRF 후보와 chunk embedding만 재사용했다.

- MMR(Maximal Marginal Relevance): 관련성은 유지하면서 이미 선택한 청크와 비슷한 청크에 벌점을 주는 순위 선택법
- redundancy(중복성): Top5 청크 사이 양의 cosine, exact duplicate, 같은 카드의 계층 간 포함 관계로 진단
- exact-document dedup: 카드와 정규화 문서가 완전히 같은 청크를 한 묶음으로 보는 평가
- answer-bearing(level-relaxed term-bearing): 기대 카드와 필수 용어를 포함하지만 level을 무시한 진단이며 사실적으로 완전한 정답과 같지 않다
- W/L/T: 같은 질의의 no-MMR 대비 승/패/동률 수
- decision: `retain_no_mmr`; primary Top50 gate 통과 λ: []
- Top20은 primary에서 선택된 같은 λ의 효율성 보조 비교로만 해석한다.

후보 밖 recall을 개선할 수 없고 cosine은 의미 중복의 대리 지표다. 정확 포함만 세므로 표현이 다른 중복은 놓친다. 개발셋 결과이며 holdout·운영 일반화를 주장하지 않는다. GPU/model/custom code/network/API/new embedding/Chroma/package install 호출은 모두 0이다.


## 구성

| 항목 | 설명 |
|---|---|
| `mmr_candidate_ceiling.csv` | 검색 후보 목록 또는 후보 coverage 산출물. |
| `mmr_contract.json` | 구조화된 평가 또는 설정 자료. |
| `mmr_integrity.json` | 실행 무결성·재현 확인 자료. |
| `mmr_level_pair_cosine.csv` | 평가 또는 검증용 표 형식 산출물. |
| `mmr_paired_deltas.csv` | 동일 질의의 paired 비교 산출물. |
| `mmr_per_query_metrics.csv` | 질의·사실별 상세 평가 산출물. |
| `mmr_redundancy_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `mmr_redundancy_summary.csv` | 집계 요약 산출물. |
| `mmr_selection_decision.json` | 구조화된 평가 또는 설정 자료. |
| `mmr_step_trace.csv` | 평가 또는 검증용 표 형식 산출물. |
| `mmr_summary.csv` | 집계 요약 산출물. |
| `mmr_summary.json` | 집계 요약 산출물. |
| `mmr_wlt.csv` | 동일 질의의 paired 비교 산출물. |
