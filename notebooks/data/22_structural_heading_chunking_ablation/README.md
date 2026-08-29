# 22. 구조 기반 청킹 + 제목 경로 검색문

개발 질의 30개 single-run 결과입니다. raw 구조로 direct body 청크를 만들고 retrieval_text에 issuer/card/heading path를 붙였으며 evidence_text에는 인위적 issuer/card를 넣지 않았습니다.

- 구조 청크 147개, hierarchy node 172개
- 신규 embedding 147개, 실제 65856 input tokens, 생성 요청 3회
- 공식 단가 계약 $0.02/1M input tokens, 계산 비용 USD 0.00131712
- 가격 출처: https://openai.com/api/pricing/
- 현재 fresh run API 요청 0회
- 기존 query embedding 30개는 읽기 전용 cache 재사용
- Chroma query/reranker/MMR/morphology/router 0

Card Hit@3은 상위 3개 안에 기대 카드가 있는 비율, Card MRR@5는 기대 카드의 첫 순위 역수 평균입니다. Evidence Hit@3/MRR@5는 level을 무시하고 expected card와 required terms가 evidence_text에 있는 청크를 기준으로 합니다. Recall/nDCG는 청크 분모 변화 때문에 진단만 합니다. W/L/T는 같은 질의의 개선/하락/동률 수입니다.

ranking_freeze 파일은 gold 전에 저장한 양쪽 Top50입니다. relevance_sets와 heading_inflation_audit는 제목 경로 부풀림 수동 감사, redundancy 파일은 same-card exact/containment 감사입니다.

판정은 candidate_for_reranker_reevaluation_on_dev입니다. 개발 후보 판단일 뿐 운영 또는 별도 일반화 결론이 아니며, 통과해도 다음 reranker 재평가 후보일 뿐입니다.

## 독립 검토 최종 disposition

계산·ranking·hash 검증은 PASS지만 사전 기술 gate의 선택 강도는 채택 근거로 부족했습니다. Evidence Hit@3는 1개 개선과 1개 하락으로 0.90→0.90 동률이고, MRR@5는 0.8791667→0.875로 0.0041667 하락했습니다. 그런데 사전의 최소 1 Hit win 절 때문에 기술 gate는 통과했습니다. 기존 gate_pass=true와 계약은 소급 변경하지 않습니다.

최종 개발 판단은 inconclusive_keep_existing_baseline_and_retain_structural_candidate_for_followup입니다. 기존 기준선을 유지하고 구조 청킹은 후속 연구 후보로만 남깁니다. 운영 적용, 별도 일반화 결론, 현재 청킹 교체는 허용하지 않습니다. 더 엄격한 gate는 후속 실험 전에 새로 선언하며 이번 결과를 소급 재판정하지 않습니다.

## Follow-up 1 — operational-prototype baseline scope correction

이 비교는 결과 후 발견한 baseline 범위 누락을 보완한 post-hoc diagnostic입니다. 기존 327개 RRF Top50에서 section/benefit만 순서 보존 필터한 current prototype 범위와 구조 청킹 Top50을 직접 비교합니다. 기존 formal selection과 independent disposition은 바꾸지 않습니다.

결과는 followup1 전용 파일에만 저장했습니다. confirmatory/promotion gate가 아니므로 formal promotion은 불가능합니다. 현재 disposition은 retain_existing_operational_prototype_baseline_pending_confirmatory_test이며 구조 청킹은 후속 후보로만 남깁니다. 기존 exact-v1과 historical notebook16 0.4 Top50이 완전 일치하지 않는 한계도 그대로 상속합니다.


## Follow-up 2 — 새 구조 BM25/RRF 27조합 탐색

기존 notebook 셀은 이번 실행에서 재실행하지 않았습니다. self-contained Follow-up 2 셀만 별도 fresh kernel에서 실행해 저장된 chunks/cache/query/Follow-up 1을 직접 읽었습니다.

27개 조합을 모두 저장했고 새 API·network·embedding·GPU·Chroma query·검색 index 변경은 0, API 비용은 $0입니다. Recall/nDCG는 진단값입니다.

gate 통과 조합 수는 0개이고 disposition은 retain_existing_operational_prototype입니다. 통과해도 개발 질의 27-grid 탐색이므로 정식 운영 승격은 불가능합니다. historical notebook16 재현 한계 30/20/28/19도 유지됩니다.
