# 21 현재 청킹 분기 전 종합 평가

- actual regex route: 정답을 보지 않고 질문 문구만 보는 실제 규칙
- selective oracle: 정답 유형을 미리 아는 상한이며 운영 후보가 아님
- Precision(정밀도): 해당 유형이라고 예측한 것 중 맞은 비율. 예: numeric 예측의 정확성
- Recall(재현율): 실제 해당 유형 중 찾아낸 비율. 표현이 바뀌면 낮아질 수 있음
- F1: Precision과 Recall의 조화평균. 작은 개발셋에서는 변동이 큼
- Specificity(특이도): rerank하지 않아야 할 질문을 passthrough한 비율
- Hit@3: 상위 3개 안에 관련 청크가 있는 비율
- Recall@5: 전체 관련 청크 중 상위 5개가 회수한 비율
- MRR@5: 첫 관련 청크가 얼마나 앞에 있는지 나타내는 평균
- nDCG@5: 관련 청크의 상위 순서 품질. 관련 청크 수에 민감함

Phase A 분류 판정: `dev_rule_exactly_reproduces_gold_route`. 항상 development_rule_only/not_validated_for_unseen_queries/not_eligible_for_promotion이다.

Phase B primary raw best: `bge_augmented`. 모델·augmentation 효과는 decision.json의 factorial_effects에 분리했다. GTE와 BGE logit 절대값은 비교하지 않는다.

저장된 17번 GTE logit 재현에서 0.002는 fp16 배치 padding 차이를 확인하는 수치 진단 한계일 뿐이다. 품질 계약은 80개 후보 pool의 전체 순위와 Top5, 그리고 18번 지표를 exact 재현하는 것이며 모두 hard assertion이다.

개발셋 단일 실행이며 현재 청킹과 저장 후보에 한정된다. GPU physical 0을 사용했고 network/API/new embedding/Chroma/package install은 0이다. 기존 pip check의 torch-setuptools 경고는 수정하지 않았다.
