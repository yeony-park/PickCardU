# 20 Selective MMR/GTE gold-type oracle

Codex coder agent가 `skn25` CPU/offline fresh kernel에서 실행한 개발셋 사후 진단이다. 정답 query type을 미리 아는 oracle이므로 실제 router가 아니다.

- selective MMR: 숫자형 질의만 MMR, 의미형·고유명사형은 no-MMR
- combined: 숫자형은 MMR, 의미형은 GTE, 고유명사형은 no-MMR
- paired delta/WLT: 같은 질의에서 기준 대비 지표 차이와 승/패/동률
- exact duplicate: 동일 카드와 동일 정규화 문서 group의 Top5 초과 중복
- combined 전체 cosine은 GTE cosine 미저장으로 N/A이며 numeric MMR 10질의만 별도 보고
- primary Top50 λ0.7은 19번 결과 후 정한 adaptive exploratory 조건
- diagnostic label: `metric_tradeoff_on_dev`
- final status: exploratory_gold_type_oracle_only / not_eligible_for_promotion / not_an_operational_router

저장된 source metric/Top5 행만 선택했으며 새 ranking·score 혼합·검색·재평가를 하지 않았다. 개발셋 단일 결과로 holdout·운영 일반화를 주장하지 않는다. GPU/model/custom code/network/API/new embedding/Chroma/package install은 모두 0이다.


## 구성

| 항목 | 설명 |
|---|---|
| `oracle_contract.json` | 구조화된 평가 또는 설정 자료. |
| `oracle_decision.json` | 구조화된 평가 또는 설정 자료. |
| `oracle_integrity.json` | 실행 무결성·재현 확인 자료. |
| `oracle_paired_deltas.csv` | 동일 질의의 paired 비교 산출물. |
| `oracle_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `oracle_structural_diagnostics.csv` | 평가 또는 검증용 표 형식 산출물. |
| `oracle_summary.csv` | 집계 요약 산출물. |
| `oracle_summary.json` | 집계 요약 산출물. |
| `oracle_wlt.csv` | 동일 질의의 paired 비교 산출물. |
