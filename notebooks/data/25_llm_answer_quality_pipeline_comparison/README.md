# 25 — LLM 답변 품질 4조합 preflight

현재 10개 카드 문서 안의 개발 질의 40개로 OLD-K3/K5와 STRUCT-K3/K5 end-to-end 패키지를 비교하기 위한 입력을 동결했습니다. K는 입력으로 주는 서로 다른 카드 근거 그룹의 최대 수입니다. direct 답변은 필요한 카드만 가변적으로 내고, recommendation 답변은 최대 K개입니다. 같은 카드-group schema와 필드 순서, 카드당 근거 최대 5개, 근거당 cl100k_base 640-token head cap을 사용합니다. 실제 context 길이 차이는 허용하며 token-matched 비교는 하지 않습니다.

OLD/STRUCT 차이는 청킹 하나만이 아니라 후보 생성, query-text routing, BGE 적용 범위와 evidence packaging을 함께 포함합니다. 따라서 이후 결과도 청킹 단독 효과로 해석하면 안 됩니다. Proper/Numeric/Semantic/AND를 각각 보고하고 direct와 recommendation을 50:50으로 평균한 family macro를 중심으로 보며 전체 micro는 보조입니다.

외부 실행은 아직 승인되지 않았습니다. exact approval core가 별도로 승인되기 전까지 Responses API 요청은 0이며, schema/refusal/incomplete 응답은 retries 0 계약 아래 실패로 남깁니다. 이번 계획은 160개 single run뿐이고 반복 320회 실행은 포함하지 않습니다.

생성 설정은 `gpt-5.6-terra`, reasoning effort `medium`, tools 0, store=false, strict structured output, 응답당 최대 1,200 output tokens로 고정했습니다. K3/K5는 같은 schema 필드 구조를 쓰되 recommendation의 `cards.maxItems`를 각각 3/5로 고정합니다. 전송 승인 상한과 비용 추정은 `payload_manifest.json` 및 `approval_core.json`에 기록합니다. 비용은 cl100k_base 토큰 수와 출력 토큰 상한으로 계산한 estimate upper bound이며 provider billing hard cap이 아닙니다. 가격 URL은 이번 offline run에서 새로 조회하지 않았습니다.

STRUCT direct 질의용 로컬 BGE 점수는 327쌍을 한 번 생성했고 모두 finite, truncation/OOM 0이었습니다. 이후 CPU 단계의 join 오류를 고친 뒤에는 같은 score cache를 327/327 exact 검증해 재사용했으며 GPU 재점수는 하지 않았습니다. 최종 fresh-kernel 검증은 API/network/GPU/model/new embedding/Chroma 0입니다. 답변 gold 160행의 blocker가 없더라도 외부 Responses 실행은 별도 명시 승인 전까지 계속 차단합니다.

응답 저장 직후 validator는 형식 실패와 transport-semantic 실패를 분리합니다. 실제 전송 card group의 발급사·카드명·evidence 소유권, citation 일관성, 최소 lexical grounding과 abstention 상태를 검사합니다. 통과 상태 `transport_semantic_validation_pass`는 사실 정답 판정이 아닙니다. 사실·수치·조건 정답은 외부 응답 뒤 `answer_quality_scores.jsonl`을 만드는 별도 gold quality scorer가 반드시 판정해야 하며, 그 전에는 외부 run을 완료로 보지 않습니다. 개별 실패는 저장·채점하며 160개 batch 전체를 중단하지 않습니다. `abstain_expected=true` 11건은 예상 abstention 계약에 결속했습니다.

노트북 무결성은 raw 파일의 자기 hash를 내부에 넣지 않습니다. cell source, cell id, cell type, skip tag와 nbformat만 canonical JSON으로 해시하고 outputs, execution_count와 실행 timing metadata는 제외합니다. 따라서 같은 소스의 fresh 실행에서 같은 `notebook_content_sha256`이 재현됩니다.
External runner의 승인·환경·hash 검사는 `assert`가 아니라 명시적 RuntimeError guard를 사용합니다. Runner source 자체를 `python -O`와 승인 env 0으로 실행해 OpenAI import/client 생성 전에 차단되는지 검증하고 audit SHA를 approval core에 포함합니다.

## Blind custom-agent review follow-up

A single custom Codex reviewer judged only the anonymous packet, before configuration-key decoding; this was not a human audit. OLD led 11-7 with 62 ties overall, while STRUCT led recommendation 6-5 but lost direct 1-6. This preserves the automatic retain-OLD development decision. OLD is an accuracy-first development candidate, not a production incumbent; switching cost is not applicable, promotion is forbidden, and dominance is not established because 62/80 pairs tied. The separate 20-row non-blind regression audit is not merged into the blind result.
