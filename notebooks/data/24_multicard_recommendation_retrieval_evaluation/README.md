# 24 다중정답 추천형 검색 개발평가 — preflight

현재 10개 문서 안에서 조건에 맞는 카드를 여러 장 찾는 개발평가입니다. 시장 전체 추천, 최신 발급 여부, 개인 자격, 상품 우열을 평가하지 않습니다.

이번 실행은 CPU/offline preflight만 완료했습니다. 질의 10개, 카드 라벨 100개(positive 33, negative 67, insufficient 0), 원자 근거 33개와 old/structural 독립 투영 66행을 동결했습니다. 순위·embedding·reranker 점수는 만들지 않았습니다.

- Card Precision: 반환한 카드 중 정답 카드 비율
- Card Recall: 정답 카드 중 반환한 비율
- Evidence-supported Card Recall: 대표 청크 하나가 hard predicate까지 증명한 정답 카드 회수율
- Evidence Accuracy: 반환 슬롯 중 카드와 근거가 모두 맞는 비율. 빈 슬롯은 실패

외부 단계는 `external_execution_approval.json`의 정확한 manifest 승인 없이는 닫혀 있습니다. BGE는 로컬 캐시만 쓰며 gold/evaluation 필드를 scorer 입력에 넣지 않습니다. 결과는 dev signal 전용입니다.


## 승인 후 전체 실행 결과

질의 10개 embedding만 승인 manifest에 따라 전송했고, 문서·gold·평가 필드는 전송하지 않았습니다. old/new Top10·20 검색과 local BGE 400쌍 평가를 완료했습니다. 선택 결과는 `retain_old_selective_bge_development_baseline`이며 개발 신호일 뿐 운영·holdout 승격이 아닙니다. Q01_C03 structural은 부모/자식으로 조건이 나뉘어 대표 단일 청크가 두 predicate를 모두 포함하지 않으면 엄격히 evidence 실패로 처리했습니다.



## 실행 이력

1. CPU/offline preflight 성공(API 0, GPU 0).
2. 승인된 query embedding 1회/525 tokens로 10×1536 cache를 만든 뒤, old duplicate content hash를 잘못 하나로 합치는 loader assertion에서 실패(BGE 0).
3. historical batch/item 위치를 authoritative source로 고친 최종 실행은 API 0/cache 재사용, BGE 400쌍 성공(batch2, OOM 없음).
4. 이 finalization은 CPU/offline이며 API·GPU·모델 재실행 0입니다.
